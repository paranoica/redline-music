//! A Karplus-Strong string: a noise burst circulating through a delay line, a two-point
//! average and a first-order allpass that tunes the fractional part of the period.

use super::fm::{param, Gate};
use super::{hz, noise, Ctx, Voice};
use crate::beat::events::Event;

const DECAY: usize = 0;
const TONE: usize = 1;
const RELEASE: usize = 2;

struct Pluck {
    /// The string's past output; a note uses the first `len` entries as a ring.
    line: Vec<f64>,
    len: usize,
    w: usize,
    active: bool,
    n: u64,
    gate: Gate,
    id: u32,
    vel: f64,
    /// The loop's two-point average weights the older sample by this.
    stretch: f64,
    gain: f64,
    allpass: f64,
    ap_in: f64,
    ap_out: f64,
    excite: f64,
    burst: u64,
    lp: f64,
}

impl Pluck {
    fn new(rate: f32) -> Pluck {
        // the longest period, MIDI 0, plus the average's extra sample and some slack
        let cap = (rate.max(1.0) as f64 / hz(0.0)).ceil() as usize + 4;
        Pluck {
            line: vec![0.0; cap],
            len: 2,
            w: 0,
            active: false,
            n: 0,
            gate: Gate::default(),
            id: 0,
            vel: 0.0,
            stretch: 0.5,
            gain: 0.0,
            allpass: 0.0,
            ap_in: 0.0,
            ap_out: 0.0,
            excite: 1.0,
            burst: 0,
            lp: 0.0,
        }
    }
}

impl Voice for Pluck {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("pluck", params, i);
        let tone = p(TONE);
        let f = hz((event.note as f64).clamp(0.0, 127.0));
        let period = rate / f;
        let w = (std::f64::consts::TAU * f / rate).min(3.0);
        let stretch = 0.5 - 0.45 * tone;
        // the average's phase delay at the fundamental, taken off the period so it stays
        // in tune at the top as well
        let avg_delay = (stretch * w.sin()).atan2(1.0 - stretch + stretch * w.cos()) / w;
        let avg_gain =
            ((1.0 - stretch + stretch * w.cos()).powi(2) + (stretch * w.sin()).powi(2)).sqrt();
        let rest = period - avg_delay;
        let max_whole = self.line.len() - 2;
        let whole = ((rest - 0.5).floor() as usize).clamp(1, max_whole);
        let frac = (rest - whole as f64).clamp(0.1, 1.9);
        // the exact allpass coefficient for that delay at the fundamental
        let allpass = ((1.0 - frac) * w / 2.0).sin() / ((1.0 + frac) * w / 2.0).sin();
        // a loop gain over one would grow without end at DC
        let per_period = 10f64.powf(-3.0 / (p(DECAY) * f));
        let gain = (per_period / avg_gain).min(0.99999);

        self.len = whole + 2;
        self.line[..self.len].fill(0.0);
        self.w = 0;
        self.active = true;
        self.n = 0;
        self.gate = Gate::new(event, 0.0, p(RELEASE), rate);
        self.id = event.id;
        self.vel = event.vel.clamp(0.0, 1.0) as f64;
        self.stretch = stretch;
        self.gain = gain;
        self.allpass = allpass.clamp(-0.99, 0.99);
        self.ap_in = 0.0;
        self.ap_out = 0.0;
        self.excite = 0.2 + 0.8 * tone;
        self.burst = whole as u64 + 1;
        self.lp = 0.0;
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        if !self.active {
            return false;
        }
        let len = self.len;
        for frame in out.iter_mut() {
            if self.gate.done(self.n) {
                break;
            }
            // with a ring of whole + 2, the slots after the write point hold y[n - whole - 1]
            // and y[n - whole]
            let older = self.line[(self.w + 1) % len];
            let old = self.line[(self.w + 2) % len];
            let avg = (1.0 - self.stretch) * old + self.stretch * older;
            let ap = self.allpass * avg + self.ap_in - self.allpass * self.ap_out;
            self.ap_in = avg;
            self.ap_out = ap;
            let mut y = self.gain * ap;
            if self.n < self.burst {
                self.lp += self.excite * (noise(self.id, self.n) as f64 - self.lp);
                y += self.lp;
            }
            self.line[self.w] = y;
            self.w = (self.w + 1) % len;
            let s = y * self.vel * self.gate.at(self.n);
            frame[0] += s as f32;
            frame[1] += s as f32;
            self.n += 1;
        }
        self.active = !self.gate.done(self.n);
        self.active
    }
}

pub fn make(instrument: &str, rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "pluck").then(|| Box::new(Pluck::new(rate)) as Box<dyn Voice>)
}
