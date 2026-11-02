//! The 808 bass: a sine at the note, punched in from above, through a drive, a clip, a fold
//! and a low-pass, held with a decay and released after the note's end, gliding in from the
//! note it cut.

use super::super::events::Event;
use super::super::instruments::instrument;
use super::drums::{add, drive, Fall, Gate, OnePole, Sine};
use super::fm::param;
use super::{hz, noise, Ctx, Voice};
use std::f64::consts::{FRAC_PI_2, PI};

const TUNE: usize = 0;
const DECAY: usize = 1;
const RELEASE: usize = 2;
const DRIVE: usize = 3;
const GLIDE_MS: usize = 4;
const CLICK: usize = 5;
const TONE: usize = 6;
const PUNCH: usize = 7;
const PUNCH_MS: usize = 8;
const CLIP: usize = 9;
const FOLD: usize = 10;

/// How close a glide gets to its note by `glide_ms`, in semitones: one cent.
const GLIDE_REACH: f64 = 0.01;
const CLICK_S: f64 = 0.003;
/// The clip's gain into the clamp at clip 1.
const CLIP_GAIN: f64 = 20.0;
/// The fold's gain at fold 1: the sine folds a full-scale peak back over twice.
const FOLD_GAIN: f64 = 5.0;

/// The RMS of a full-scale sine with gain `g` clamped at ±1.
fn clipped_sine_rms(g: f64) -> f64 {
    if g <= 1.0 {
        return g * 0.5f64.sqrt();
    }
    let a = (1.0 / g).asin();
    let mean_square = (2.0 / PI) * (g * g * (a / 2.0 - (2.0 * a).sin() / 4.0) + (FRAC_PI_2 - a));
    mean_square.sqrt()
}

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
    /// semitones above the note at the hit, shrinking by `punch_fall` a frame
    punch: f64,
    punch_fall: f64,
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
    /// the gain into the clamp and the level after it; no clip at a gain of 0
    clip_gain: f64,
    clip_level: f64,
    fold: f64,
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
        // read through `param`: a score from before these params has fewer of them
        self.punch = if e.legato_from.is_some() {
            0.0
        } else {
            param("bass808", p, PUNCH)
        };
        let tau = (param("bass808", p, PUNCH_MS) / 1000.0 * rate).max(1.0);
        self.punch_fall = (-1.0 / tau).exp();
        let clip = param("bass808", p, CLIP);
        (self.clip_gain, self.clip_level) = if clip > 0.0 {
            let g = 1.0 + (CLIP_GAIN - 1.0) * clip;
            // a full-scale sine comes out of the clamp as loud as it went in
            (g, 0.5f64.sqrt() / clipped_sine_rms(g))
        } else {
            (0.0, 1.0)
        };
        self.fold = param("bass808", p, FOLD);
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
            let f = if self.punch > 0.0 {
                let f = hz(self.note + self.away + self.punch);
                self.punch *= self.punch_fall;
                f
            } else {
                hz(self.note + self.away)
            };
            self.away *= self.glide;
            let x = self.sine.tick(f, rate) * env
                + noise(self.id, k) as f64 * self.click_env.next() * self.click;
            let mut y = drive(x, self.drive);
            if self.clip_gain > 0.0 {
                y = (self.clip_gain * y).clamp(-1.0, 1.0) * self.clip_level;
            }
            if self.fold > 0.0 {
                let folded = (FRAC_PI_2 * (1.0 + (FOLD_GAIN - 1.0) * self.fold) * y).sin();
                y += self.fold * (folded - y);
            }
            add(o, self.tone.tick(y) * g);
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
