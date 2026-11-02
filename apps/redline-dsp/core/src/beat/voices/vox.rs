//! A choir: detuned saws a note, one a singer spread across the field, with a little breath,
//! through three parallel formant band-passes that morph between the vowels.

use super::fm::{param, Gate};
use super::osc::{saw, Svf};
use super::{hz, noise, Ctx, Voice};
use crate::beat::events::{hash, unit, Event};
use std::f64::consts::{FRAC_1_SQRT_2, FRAC_PI_4, TAU};

const VOWEL: usize = 0;
const VOICES: usize = 1;
const DETUNE: usize = 2;
const BREATH: usize = 3;
const VIBRATO: usize = 4;
const ATTACK: usize = 5;
const RELEASE: usize = 6;

const MAX_SINGERS: usize = 7;

/// The first three formants of a, e, i, o, u, in Hz.
const FORMANTS: [[f64; 3]; 5] = [
    [800.0, 1150.0, 2900.0],
    [400.0, 1600.0, 2700.0],
    [350.0, 1700.0, 2700.0],
    [450.0, 800.0, 2830.0],
    [325.0, 700.0, 2530.0],
];
const BANDWIDTHS: [f64; 3] = [80.0, 90.0, 120.0];
/// The higher formants are the weaker.
const FORMANT_GAINS: [f64; 3] = [1.0, 0.5, 0.25];
/// The formants pass a few harmonics of the saws: this brings a singer near full scale.
const LEVEL: f64 = 3.0;
const BREATH_LEVEL: f64 = 0.5;
const VIBRATO_HZ: f64 = 5.0;
const VIBRATO_CENTS: f64 = 30.0;

#[derive(Default)]
struct Vox {
    active: bool,
    n: u64,
    rate: f64,
    gate: Gate,
    id: u32,
    vel: f64,
    f: f64,
    count: usize,
    phase: [f64; MAX_SINGERS],
    /// each singer's pitch over the note's
    spread: [f64; MAX_SINGERS],
    pan: [[f64; 2]; MAX_SINGERS],
    breath: f64,
    vibrato: f64,
    formants: [[Svf; 3]; 2],
}

impl Voice for Vox {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("vox", params, i);
        let count = (p(VOICES).round() as usize).clamp(1, MAX_SINGERS);
        let detune = p(DETUNE);
        let level = 1.0 / (count as f64).sqrt();
        let mut phase = [0.0; MAX_SINGERS];
        let mut spread = [1.0; MAX_SINGERS];
        let mut pan = [[0.0; 2]; MAX_SINGERS];
        for i in 0..count {
            // -1 for the lowest singer, +1 for the highest, both for detune and for place
            let s = if count > 1 {
                2.0 * i as f64 / (count - 1) as f64 - 1.0
            } else {
                0.0
            };
            phase[i] = unit(hash(&[u64::from(event.id), i as u64]));
            spread[i] = (detune * s / 1200.0).exp2();
            // cos and sin of a quarter pi differ in the last bit; one singer is centred exactly
            pan[i] = if count > 1 {
                let angle = (s + 1.0) * FRAC_PI_4;
                [level * angle.cos(), level * angle.sin()]
            } else {
                [FRAC_1_SQRT_2; 2]
            };
        }
        let vowel = p(VOWEL);
        let at = (vowel.floor() as usize).min(FORMANTS.len() - 2);
        let mix = vowel - at as f64;
        let mut bank = [Svf::default(); 3];
        for (j, b) in bank.iter_mut().enumerate() {
            let centre = FORMANTS[at][j] + mix * (FORMANTS[at + 1][j] - FORMANTS[at][j]);
            *b = Svf::new(centre, centre / BANDWIDTHS[j], rate);
        }
        *self = Vox {
            active: true,
            n: 0,
            rate,
            gate: Gate::new(event, p(ATTACK), p(RELEASE), rate),
            id: event.id,
            vel: event.vel.clamp(0.0, 1.0) as f64,
            f: hz(event.note as f64),
            count,
            phase,
            spread,
            pan,
            breath: p(BREATH) * BREATH_LEVEL,
            vibrato: p(VIBRATO) * VIBRATO_CENTS,
            formants: [bank; 2],
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
            let t = self.n as f64 / self.rate;
            let cents = self.vibrato * (TAU * VIBRATO_HZ * t).sin();
            let f = self.f * (cents / 1200.0).exp2();
            let air = self.breath * noise(self.id, self.n) as f64;
            let mut mix = [air; 2];
            for i in 0..self.count {
                let dt = (f * self.spread[i] / self.rate).min(0.45);
                let s = saw(self.phase[i], dt);
                mix[0] += s * self.pan[i][0];
                mix[1] += s * self.pan[i][1];
                self.phase[i] = (self.phase[i] + dt).fract();
            }
            let g = LEVEL * self.vel * self.gate.at(self.n);
            for (c, x) in mix.iter().enumerate() {
                let mut y = 0.0;
                for (b, gain) in self.formants[c].iter_mut().zip(FORMANT_GAINS) {
                    y += gain * b.tick(*x).band;
                }
                frame[c] += (y * g) as f32;
            }
            self.n += 1;
        }
        self.active = !self.gate.done(self.n);
        self.active
    }
}

pub fn make(instrument: &str, _rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "vox").then(|| Box::new(Vox::default()) as Box<dyn Voice>)
}
