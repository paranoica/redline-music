//! The reese: two band-limited saws detuned against each other over a sine an octave below,
//! through a drive and a resonant low-pass, one voice gliding in from the note it cut.

use super::drums::drive;
use super::fm::{param, Gate};
use super::osc::{saw, Svf};
use super::{hz, Ctx, Voice};
use crate::beat::events::{hash, unit, Event};
use std::f64::consts::TAU;

const TUNE: usize = 0;
const DETUNE: usize = 1;
const CUTOFF: usize = 2;
const RESONANCE: usize = 3;
const SUB: usize = 4;
const DRIVE: usize = 5;
const GLIDE_MS: usize = 6;
const ATTACK: usize = 7;
const RELEASE: usize = 8;

/// How close a glide gets to its note by `glide_ms`, in semitones: one cent.
const GLIDE_REACH: f64 = 0.01;
/// Two saws and a sub sum well over an 808's sine: this brings it to an 808's level.
const LEVEL: f64 = 0.5;

#[derive(Default)]
struct Reese {
    active: bool,
    n: u64,
    rate: f64,
    gate: Gate,
    vel: f64,
    note: f64,
    /// semitones from the note, shrinking by `glide` a frame
    away: f64,
    glide: f64,
    /// each saw's pitch over the note's
    spread: [f64; 2],
    phase: [f64; 2],
    sub_phase: f64,
    sub: f64,
    drive: f64,
    filter: Svf,
}

impl Voice for Reese {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("reese", params, i);
        // the tune is on both notes, so the distance is the notes' own
        let away = event
            .legato_from
            .map_or(0.0, |from| (from - event.note) as f64);
        let frames = (p(GLIDE_MS) / 1000.0 * rate).max(1.0);
        let glide = if away.abs() > GLIDE_REACH {
            (-(away.abs() / GLIDE_REACH).ln() / frames).exp()
        } else {
            0.0
        };
        let half = p(DETUNE) / 2.0 / 1200.0;
        // a legato note carries on the one it cut, at its level: no new attack
        let attack = if event.legato_from.is_some() {
            0.0
        } else {
            p(ATTACK)
        };
        // resonance 0 is a Butterworth, 1 a Q of 10
        let q = 1.0 / (std::f64::consts::SQRT_2 * (1.0 - p(RESONANCE)) + 0.1 * p(RESONANCE));
        *self = Reese {
            active: true,
            n: 0,
            rate,
            gate: Gate::new(event, attack, p(RELEASE), rate),
            vel: event.vel.clamp(0.0, 1.0) as f64,
            note: event.note as f64 + p(TUNE),
            away,
            glide,
            spread: [(-half).exp2(), half.exp2()],
            phase: [0, 1].map(|i| unit(hash(&[u64::from(event.id), i]))),
            sub_phase: 0.0,
            sub: p(SUB),
            drive: p(DRIVE),
            filter: Svf::new(p(CUTOFF), q, rate),
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
            let f = hz(self.note + self.away);
            self.away *= self.glide;
            let mut x = 0.0;
            for i in 0..2 {
                let dt = (f * self.spread[i] / self.rate).min(0.45);
                x += 0.5 * saw(self.phase[i], dt);
                self.phase[i] = (self.phase[i] + dt).fract();
            }
            x += self.sub * (TAU * self.sub_phase).sin();
            self.sub_phase = (self.sub_phase + (0.5 * f / self.rate).min(0.45)).fract();
            let low = self.filter.tick(drive(x, self.drive)).low;
            let y = LEVEL * low * self.vel * self.gate.at(self.n);
            frame[0] += y as f32;
            frame[1] += y as f32;
            self.n += 1;
        }
        self.active = !self.gate.done(self.n);
        self.active
    }
}

pub fn make(instrument: &str, _rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "reese").then(|| Box::new(Reese::default()) as Box<dyn Voice>)
}
