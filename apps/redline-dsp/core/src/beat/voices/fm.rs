//! Two-operator FM. The preset picks the envelope shapes; `ratio` and `index` are used as
//! given, so a bell wants an inharmonic ratio such as 1.4. Glass adds a third operator, a
//! second modulator at a fixed high ratio.

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
const PLUCK: usize = 2;
const BRASS: usize = 3;

/// Brass's modulator lags its carrier: it swells over the attack and this much longer.
const BRASS_SWELL_S: f64 = 0.05;
/// The share of the swell's peak brass's modulator settles to.
const BRASS_SUSTAIN: f64 = 0.6;
/// Glass's second modulator over the carrier, whole so its partials stay harmonic.
const GLASS_RATIO: f64 = 7.0;
/// Its index against `index`.
const GLASS_INDEX: f64 = 0.5;
const HELD_LEVEL: f64 = 0.5;

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
    /// frames the modulator swells over before it falls; 0 for no swell
    swell: f64,
    /// where the modulator's fall settles, as a share of its peak
    floor_m: f64,
    /// the second modulator; none at an index of 0
    modulator2: f64,
    dm2: f64,
    index2: f64,
    env_m2: f64,
    fall_m2: f64,
    /// the held presets (brass, glass) sit lower, since their carrier does not decay
    level: f64,
}

impl Voice for Fm {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("fm", params, i);
        let decay = p(DECAY);
        let f = hz(event.note as f64);
        let vel = event.vel.clamp(0.0, 1.0) as f64;
        let preset = p(PRESET).round() as usize;
        let (fall_c, fall_m) = match preset {
            BELL => (fall(decay, rate), fall(decay, rate)),
            // an electric piano: the tine's brightness goes fast, the tone holds
            KEYS => (1.0, fall(decay / 4.0, rate)),
            PLUCK => (fall(decay / 4.0, rate), fall(decay / 8.0, rate)),
            BRASS => (1.0, fall(decay, rate)),
            // glass: the partials ring off fast over a carrier that holds
            _ => (1.0, fall(decay / 8.0, rate)),
        };
        let mut index = p(INDEX);
        let (mut swell, mut floor_m, mut index2) = (0.0, 0.0, 0.0);
        if preset == BRASS {
            swell = ((p(ATTACK) + BRASS_SWELL_S) * rate).max(1.0);
            floor_m = BRASS_SUSTAIN;
            // a harder blow is a brassier one
            index *= 0.3 + 0.7 * vel;
        } else if preset > BRASS {
            index2 = GLASS_INDEX * index;
        }
        *self = Fm {
            active: true,
            n: 0,
            gate: Gate::new(event, p(ATTACK), p(RELEASE), rate),
            vel,
            carrier: 0.0,
            modulator: 0.0,
            dc: f / rate,
            dm: f * p(RATIO) / rate,
            index,
            env_c: 1.0,
            env_m: 1.0,
            fall_c,
            fall_m,
            swell,
            floor_m,
            modulator2: 0.0,
            dm2: f * GLASS_RATIO / rate,
            index2,
            env_m2: 1.0,
            fall_m2: fall(decay / 3.0, rate),
            level: if preset >= BRASS { HELD_LEVEL } else { 1.0 },
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
            let n = self.n as f64;
            let env_m = if self.swell == 0.0 {
                self.env_m
            } else if n < self.swell {
                n / self.swell
            } else {
                self.floor_m + (1.0 - self.floor_m) * self.env_m
            };
            let mut m = self.index * env_m * (TAU * self.modulator).sin();
            if self.index2 != 0.0 {
                m += self.index2 * self.env_m2 * (TAU * self.modulator2).sin();
                self.modulator2 = (self.modulator2 + self.dm2).fract();
                self.env_m2 *= self.fall_m2;
            }
            let y = (TAU * self.carrier + m).sin()
                * self.env_c
                * self.vel
                * self.gate.at(self.n)
                * self.level;
            frame[0] += y as f32;
            frame[1] += y as f32;
            self.carrier = (self.carrier + self.dc).fract();
            self.modulator = (self.modulator + self.dm).fract();
            self.env_c *= self.fall_c;
            if n >= self.swell {
                self.env_m *= self.fall_m;
            }
            self.n += 1;
        }
        self.active = !self.gate.done(self.n);
        self.active
    }
}

pub fn make(instrument: &str, _rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "fm").then(|| Box::new(Fm::default()) as Box<dyn Voice>)
}
