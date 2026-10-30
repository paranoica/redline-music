//! A supersaw: detuned band-limited saws spread across the stereo field, through a resonant
//! low-pass.

use super::fm::{param, Gate};
use super::{hz, Ctx, Voice};
use crate::beat::events::{hash, unit, Event};
use std::f64::consts::{FRAC_PI_4, PI};

const VOICES: usize = 0;
const DETUNE: usize = 1;
const CUTOFF: usize = 2;
const RESONANCE: usize = 3;
const ATTACK: usize = 4;
const RELEASE: usize = 5;

const MAX_SAWS: usize = 9;

/// PolyBLEP: the residual that rounds off a saw's jump at phase 0.
fn blep(t: f64, dt: f64) -> f64 {
    if t < dt {
        let x = t / dt;
        2.0 * x - x * x - 1.0
    } else if t > 1.0 - dt {
        let x = (t - 1.0) / dt;
        x * x + 2.0 * x + 1.0
    } else {
        0.0
    }
}

/// A state-variable low-pass (topology-preserving transform).
#[derive(Clone, Copy, Default)]
struct Svf {
    ic1: f64,
    ic2: f64,
}

impl Svf {
    fn lowpass(&mut self, x: f64, a1: f64, a2: f64, a3: f64) -> f64 {
        let v3 = x - self.ic2;
        let v1 = a1 * self.ic1 + a2 * v3;
        let v2 = self.ic2 + a2 * self.ic1 + a3 * v3;
        self.ic1 = 2.0 * v1 - self.ic1;
        self.ic2 = 2.0 * v2 - self.ic2;
        v2
    }
}

#[derive(Default)]
struct Saw {
    active: bool,
    n: u64,
    gate: Gate,
    vel: f64,
    count: usize,
    phase: [f64; MAX_SAWS],
    inc: [f64; MAX_SAWS],
    pan: [[f64; 2]; MAX_SAWS],
    svf: [Svf; 2],
    a: [f64; 3],
}

impl Voice for Saw {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("saw", params, i);
        let count = (p(VOICES).round() as usize).clamp(1, MAX_SAWS);
        let detune = p(DETUNE);
        let f = hz(event.note as f64);
        let level = 1.0 / (count as f64).sqrt();
        let mut phase = [0.0; MAX_SAWS];
        let mut inc = [0.0; MAX_SAWS];
        let mut pan = [[0.0; 2]; MAX_SAWS];
        for i in 0..count {
            // -1 for the lowest saw, +1 for the highest, both for detune and for place
            let spread = if count > 1 {
                2.0 * i as f64 / (count - 1) as f64 - 1.0
            } else {
                0.0
            };
            phase[i] = unit(hash(&[u64::from(event.id), i as u64]));
            inc[i] = (f * (detune * spread / 1200.0).exp2() / rate).min(0.45);
            let angle = (spread + 1.0) * FRAC_PI_4;
            pan[i] = [level * angle.cos(), level * angle.sin()];
        }
        let cutoff = p(CUTOFF).min(0.45 * rate);
        let g = (PI * cutoff / rate).tan();
        // resonance 0 is a Butterworth, 1 a Q of 10
        let k = std::f64::consts::SQRT_2 * (1.0 - p(RESONANCE)) + 0.1 * p(RESONANCE);
        let a1 = 1.0 / (1.0 + g * (g + k));
        *self = Saw {
            active: true,
            n: 0,
            gate: Gate::new(event, p(ATTACK), p(RELEASE), rate),
            vel: event.vel.clamp(0.0, 1.0) as f64,
            count,
            phase,
            inc,
            pan,
            svf: [Svf::default(); 2],
            a: [a1, g * a1, g * g * a1],
        };
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        if !self.active {
            return false;
        }
        let [a1, a2, a3] = self.a;
        for frame in out.iter_mut() {
            if self.gate.done(self.n) {
                break;
            }
            let mut mix = [0.0f64; 2];
            for i in 0..self.count {
                let t = self.phase[i];
                let s = 2.0 * t - 1.0 - blep(t, self.inc[i]);
                mix[0] += s * self.pan[i][0];
                mix[1] += s * self.pan[i][1];
                self.phase[i] = (t + self.inc[i]).fract();
            }
            let g = self.vel * self.gate.at(self.n);
            for (c, m) in mix.iter().enumerate() {
                frame[c] += (self.svf[c].lowpass(*m, a1, a2, a3) * g) as f32;
            }
            self.n += 1;
        }
        self.active = !self.gate.done(self.n);
        self.active
    }
}

pub fn make(instrument: &str, _rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "saw").then(|| Box::new(Saw::default()) as Box<dyn Voice>)
}
