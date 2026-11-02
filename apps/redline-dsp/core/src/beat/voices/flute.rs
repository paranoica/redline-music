//! A flute: a sine with a few weak overtones, breath noise band-passed around the pitch,
//! and a vibrato that waits, then fades in.

use super::fm::{param, Gate};
use super::osc::Svf;
use super::{hz, noise, Ctx, Voice};
use crate::beat::events::Event;
use std::f64::consts::TAU;

const BREATH: usize = 0;
const VIBRATO: usize = 1;
const VIBRATO_HZ: usize = 2;
const TONE: usize = 3;
const ATTACK: usize = 4;
const RELEASE: usize = 5;

/// The 2nd, 3rd and 4th harmonics at tone 1, against the fundamental.
const OVERTONES: [f64; 3] = [0.25, 0.12, 0.06];
const VIBRATO_CENTS: f64 = 40.0;
/// A player holds the note straight before the vibrato comes in.
const VIBRATO_DELAY_S: f64 = 0.15;
const VIBRATO_FADE_S: f64 = 0.3;
const BREATH_Q: f64 = 2.0;
/// The band-pass keeps a sliver of the noise: this makes breath 1 plainly heard.
const BREATH_LEVEL: f64 = 3.0;
/// A held sine reads louder than the decaying voices: this sits it beside a saw lead.
const LEVEL: f64 = 0.55;

#[derive(Default)]
struct Flute {
    active: bool,
    n: u64,
    rate: f64,
    gate: Gate,
    id: u32,
    vel: f64,
    f: f64,
    phase: f64,
    tone: f64,
    /// the overtones and the fundamental brought back to full scale
    norm: f64,
    breath: f64,
    air: Svf,
    vibrato: f64,
    vibrato_hz: f64,
}

impl Voice for Flute {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("flute", params, i);
        let f = hz(event.note as f64);
        let tone = p(TONE);
        *self = Flute {
            active: true,
            n: 0,
            rate,
            gate: Gate::new(event, p(ATTACK), p(RELEASE), rate),
            id: event.id,
            vel: event.vel.clamp(0.0, 1.0) as f64,
            f,
            phase: 0.0,
            tone,
            norm: 1.0 / (1.0 + tone * OVERTONES.iter().sum::<f64>()),
            breath: p(BREATH) * BREATH_LEVEL,
            air: Svf::new(f, BREATH_Q, rate),
            vibrato: p(VIBRATO) * VIBRATO_CENTS,
            vibrato_hz: p(VIBRATO_HZ),
        };
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        if !self.active {
            return false;
        }
        let limit = 0.45 * self.rate;
        for frame in out.iter_mut() {
            if self.gate.done(self.n) {
                break;
            }
            let t = self.n as f64 / self.rate;
            let since = t - VIBRATO_DELAY_S;
            let f = if self.vibrato > 0.0 && since > 0.0 {
                let depth = self.vibrato * (since / VIBRATO_FADE_S).min(1.0);
                let cents = depth * (TAU * self.vibrato_hz * since).sin();
                self.f * (cents / 1200.0).exp2()
            } else {
                self.f
            };
            let mut y = (TAU * self.phase).sin();
            if self.tone > 0.0 {
                for (k, a) in (2..).zip(OVERTONES) {
                    // an overtone past what the rate holds would fold back
                    if k as f64 * f < limit {
                        y += self.tone * a * (TAU * (k as f64 * self.phase).fract()).sin();
                    }
                }
            }
            y *= self.norm;
            let air = self.air.tick(noise(self.id, self.n) as f64).band;
            y += self.breath * air;
            self.phase = (self.phase + (f / self.rate).min(0.45)).fract();
            let y = LEVEL * y * self.vel * self.gate.at(self.n);
            frame[0] += y as f32;
            frame[1] += y as f32;
            self.n += 1;
        }
        self.active = !self.gate.done(self.n);
        self.active
    }
}

pub fn make(instrument: &str, _rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "flute").then(|| Box::new(Flute::default()) as Box<dyn Voice>)
}
