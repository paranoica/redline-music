//! Two-operator FM. The preset picks the envelope shapes; `ratio` and `index` are used as
//! given, so a bell wants an inharmonic ratio such as 1.4.

use super::{hz, Ctx, Voice};
use crate::beat::events::Event;
use crate::beat::instruments::instrument;
use std::f64::consts::TAU;

const PRESET: usize = 0;
const RATIO: usize = 1;
const INDEX: usize = 2;
const DECAY: usize = 3;
const ATTACK: usize = 4;
const RELEASE: usize = 5;

const BELL: usize = 0;
const KEYS: usize = 1;

/// The fade a cut voice (choke, steal, mono) takes to silence.
pub(super) const FADE_S: f64 = 0.005;

/// Param `i` of the instrument, clamped to its range; the default where it is missing or
/// not a number.
pub(super) fn param(name: &str, params: &[f32], i: usize) -> f64 {
    let Some(spec) = instrument(name).and_then(|inst| inst.params.get(i)) else {
        return 0.0;
    };
    let v = params.get(i).copied().filter(|v| v.is_finite());
    v.unwrap_or(spec.default).clamp(spec.low, spec.high) as f64
}

/// A gain of 1.0 every frame of 60 dB over `seconds`.
pub(super) fn fall(seconds: f64, rate: f64) -> f64 {
    10f64.powf(-3.0 / (seconds * rate).max(1.0))
}

/// Attack, release after the note's end and the short fade at a cut, all from the frame
/// since the event began, so it needs no state.
#[derive(Clone, Copy, Default)]
pub(super) struct Gate {
    attack: f64,
    release: f64,
    fade: f64,
    off: u64,
    end: u64,
    /// The first frame of silence for good.
    last: u64,
}

impl Gate {
    pub(super) fn new(event: &Event, attack_s: f64, release_s: f64, rate: f64) -> Gate {
        let attack = (attack_s * rate).max(1.0);
        let release = (release_s * rate).max(1.0);
        let fade = (FADE_S * rate).max(1.0);
        // a note without an end is let go once its attack is done
        let off = event
            .off
            .map_or(attack.ceil() as u64, |off| off.saturating_sub(event.start));
        let end = event
            .end
            .map_or(u64::MAX, |end| end.saturating_sub(event.start));
        let last = off
            .saturating_add(release.ceil() as u64)
            .min(end.saturating_add(fade.ceil() as u64));
        Gate {
            attack,
            release,
            fade,
            off,
            end,
            last,
        }
    }

    pub(super) fn at(&self, n: u64) -> f64 {
        if n >= self.last {
            return 0.0;
        }
        let mut g = (n.min(self.off) as f64 / self.attack).min(1.0);
        if n >= self.off {
            let x = (1.0 - (n - self.off) as f64 / self.release).max(0.0);
            g *= x * x;
        }
        if n >= self.end {
            g *= (1.0 - (n - self.end) as f64 / self.fade).max(0.0);
        }
        g
    }

    pub(super) fn done(&self, n: u64) -> bool {
        n >= self.last
    }
}

#[derive(Default)]
struct Fm {
    active: bool,
    n: u64,
    gate: Gate,
    vel: f64,
    carrier: f64,
    modulator: f64,
    dc: f64,
    dm: f64,
    index: f64,
    env_c: f64,
    env_m: f64,
    fall_c: f64,
    fall_m: f64,
}

impl Voice for Fm {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("fm", params, i);
        let decay = p(DECAY);
        let f = hz(event.note as f64);
        let (fall_c, fall_m) = match p(PRESET).round() as usize {
            BELL => (fall(decay, rate), fall(decay, rate)),
            // an electric piano: the tine's brightness goes fast, the tone holds
            KEYS => (1.0, fall(decay / 4.0, rate)),
            _ => (fall(decay / 4.0, rate), fall(decay / 8.0, rate)),
        };
        *self = Fm {
            active: true,
            n: 0,
            gate: Gate::new(event, p(ATTACK), p(RELEASE), rate),
            vel: event.vel.clamp(0.0, 1.0) as f64,
            carrier: 0.0,
            modulator: 0.0,
            dc: f / rate,
            dm: f * p(RATIO) / rate,
            index: p(INDEX),
            env_c: 1.0,
            env_m: 1.0,
            fall_c,
            fall_m,
        };
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        if !self.active {
            return false;
        }
        for frame in out.iter_mut() {
            if self.gate.done(self.n) {
                break;
            }
            let m = self.index * self.env_m * (TAU * self.modulator).sin();
            let y = (TAU * self.carrier + m).sin() * self.env_c * self.vel * self.gate.at(self.n);
            frame[0] += y as f32;
            frame[1] += y as f32;
            self.carrier = (self.carrier + self.dc).fract();
            self.modulator = (self.modulator + self.dm).fract();
            self.env_c *= self.fall_c;
            self.env_m *= self.fall_m;
            self.n += 1;
        }
        self.active = !self.gate.done(self.n);
        self.active
    }
}

pub fn make(instrument: &str, _rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "fm").then(|| Box::new(Fm::default()) as Box<dyn Voice>)
}
