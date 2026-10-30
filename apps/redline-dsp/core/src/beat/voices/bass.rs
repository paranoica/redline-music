//! The 808 bass: a sine at the note through a drive and a low-pass, held with a decay and
//! released after the note's end, gliding in from the note it cut.

use super::super::events::Event;
use super::super::instruments::instrument;
use super::drums::{add, drive, Fall, Gate, OnePole, Sine};
use super::{hz, noise, Ctx, Voice};

const TUNE: usize = 0;
const DECAY: usize = 1;
const RELEASE: usize = 2;
const DRIVE: usize = 3;
const GLIDE_MS: usize = 4;
const CLICK: usize = 5;
const TONE: usize = 6;

/// How close a glide gets to its note by `glide_ms`, in semitones: one cent.
const GLIDE_REACH: f64 = 0.01;
const CLICK_S: f64 = 0.003;

#[derive(Default)]
struct Bass808 {
    rate: f64,
    max_life_s: f64,
    id: u32,
    gate: Gate,
    sine: Sine,
    note: f64,
    /// semitones from the note, shrinking by `glide` a frame
    away: f64,
    glide: f64,
    held: Fall,
    /// frames from the start to the note's end
    off: Option<u64>,
    release_s: f64,
    release: Fall,
    /// the held level the release falls from
    level: f64,
    released: bool,
    click_env: Fall,
    click: f64,
    drive: f64,
    tone: OnePole,
}

impl Voice for Bass808 {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.rate;
        let off = e.off.map(|o| o.saturating_sub(e.start));
        let life = off.map(|o| o + (self.max_life_s * rate).round() as u64);
        self.gate = Gate::new(e, rate, life);
        self.id = e.id;
        self.sine = Sine::default();
        self.note = e.note as f64 + p[TUNE] as f64;
        // the tune is on both notes, so the distance is the notes' own
        self.away = e
            .legato_from
            .map_or(0.0, |from| from as f64 - e.note as f64);
        let frames = (p[GLIDE_MS] as f64 / 1000.0 * rate).max(1.0);
        self.glide = if self.away.abs() > GLIDE_REACH {
            (-(self.away.abs() / GLIDE_REACH).ln() / frames).exp()
        } else {
            0.0
        };
        self.held = Fall::new(p[DECAY] as f64 * (e.decay as f64).max(0.0), rate);
        self.off = off;
        self.release_s = p[RELEASE] as f64;
        self.release = Fall::default();
        self.level = 0.0;
        self.released = false;
        // A legato note carries on the note it cut, which this voice never saw: it starts
        // at the full held level and without a click, as if the envelope had not restarted.
        self.click = if e.legato_from.is_some() {
            0.0
        } else {
            p[CLICK] as f64
        };
        self.click_env = Fall::new(CLICK_S, rate);
        self.drive = p[DRIVE] as f64;
        self.tone = OnePole::lowpass(p[TONE] as f64, rate);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        let rate = self.rate;
        for o in out.iter_mut() {
            let Some((k, g)) = self.gate.next() else {
                return false;
            };
            if !self.released && self.off.is_some_and(|off| k >= off) {
                self.released = true;
                self.level = self.held.next();
                self.release = Fall::new(self.release_s, rate);
            }
            let env = if self.released {
                self.level * self.release.next()
            } else {
                self.held.next()
            };
            // a fall is zero only once it has ended
            if env == 0.0 && self.click_env.done() {
                return false;
            }
            let f = hz(self.note + self.away);
            self.away *= self.glide;
            let x = self.sine.tick(f, rate) * env
                + noise(self.id, k) as f64 * self.click_env.next() * self.click;
            add(o, self.tone.tick(drive(x, self.drive)) * g);
        }
        true
    }
}

pub fn make(name: &str, rate: f32) -> Option<Box<dyn Voice>> {
    if name != "bass808" {
        return None;
    }
    Some(Box::new(Bass808 {
        rate: rate as f64,
        max_life_s: instrument(name).map_or(0.0, |i| i.max_life_s as f64),
        ..Bass808::default()
    }))
}
