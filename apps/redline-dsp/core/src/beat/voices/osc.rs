//! Oscillator and filter pieces several voices share.

use super::drums::safe_hz;
use std::f64::consts::PI;

/// PolyBLEP: the residual that rounds off a saw's jump at phase 0.
pub(super) fn blep(t: f64, dt: f64) -> f64 {
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

/// A band-limited saw from phase `t` advancing `dt` a frame.
pub(super) fn saw(t: f64, dt: f64) -> f64 {
    2.0 * t - 1.0 - blep(t, dt)
}

/// A state-variable filter (topology-preserving transform), stable up to the safe band's top.
#[derive(Clone, Copy, Default)]
pub(super) struct Svf {
    a: [f64; 3],
    k: f64,
    ic1: f64,
    ic2: f64,
}

/// One sample through the filter: its low-pass, its band-pass (unity at the centre) and its
/// high-pass.
pub(super) struct Bands {
    pub low: f64,
    pub band: f64,
    pub high: f64,
}

impl Svf {
    pub(super) fn new(f: f64, q: f64, rate: f64) -> Svf {
        let g = (PI * safe_hz(f, rate) / rate).tan();
        let k = 1.0 / q;
        let a1 = 1.0 / (1.0 + g * (g + k));
        Svf {
            a: [a1, g * a1, g * g * a1],
            k,
            ic1: 0.0,
            ic2: 0.0,
        }
    }

    pub(super) fn tick(&mut self, x: f64) -> Bands {
        let [a1, a2, a3] = self.a;
        let v3 = x - self.ic2;
        let v1 = a1 * self.ic1 + a2 * v3;
        let v2 = self.ic2 + a2 * self.ic1 + a3 * v3;
        self.ic1 = 2.0 * v1 - self.ic1;
        self.ic2 = 2.0 * v2 - self.ic2;
        Bands {
            low: v2,
            band: self.k * v1,
            high: x - self.k * v1 - v2,
        }
    }
}
