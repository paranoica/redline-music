//! The drum machine voices: kick, snare, clap, closed and open hats, rim and cowbell, each
//! after the classic analog recipe. Every envelope reaches exactly zero at its length, so a
//! hit ends instead of trailing off, and a voice is silent for good once its envelopes are.

use super::super::events::Event;
use super::super::instruments::instrument;
use super::{noise, Ctx, Voice};
use std::f64::consts::TAU;

/// The level a fall is shifted down by: -60 dB, the usual meaning of a decay time.
const FLOOR: f64 = 1e-3;
/// The fade a cut voice takes.
const FADE_S: f64 = 0.005;

/// An exponential fall from 1 that reaches exactly 0 after its length.
#[derive(Clone, Copy, Default)]
pub(super) struct Fall {
    e: f64,
    coef: f64,
    left: u64,
}

impl Fall {
    pub(super) fn new(seconds: f64, rate: f64) -> Fall {
        let len = (seconds * rate).round().max(0.0) as u64;
        Fall {
            e: 1.0,
            coef: if len > 0 {
                FLOOR.powf(1.0 / len as f64)
            } else {
                0.0
            },
            left: len,
        }
    }

    pub(super) fn next(&mut self) -> f64 {
        if self.left == 0 {
            return 0.0;
        }
        let v = (self.e - FLOOR) / (1.0 - FLOOR);
        self.e *= self.coef;
        self.left -= 1;
        v
    }

    pub(super) fn done(&self) -> bool {
        self.left == 0
    }
}

/// The frame count, the level and the cut of a playing event.
#[derive(Clone, Copy, Default)]
pub(super) struct Gate {
    k: u64,
    stop: u64,
    fade: u64,
    gain: f64,
}

impl Gate {
    /// `life` is the frame the event must be silent by on its own, if it has one.
    pub(super) fn new(event: &Event, rate: f64, life: Option<u64>) -> Gate {
        let fade = ((FADE_S * rate).floor() as u64).max(1);
        let end = event
            .end
            .map_or(u64::MAX, |e| e.saturating_sub(event.start));
        let stop = life.map_or(end, |l| end.min(l.saturating_sub(fade)));
        Gate {
            k: 0,
            stop,
            fade,
            gain: velocity_gain(event.vel),
        }
    }

    /// The frame since the event's start and the gain on it, or None once the cut is over.
    pub(super) fn next(&mut self) -> Option<(u64, f64)> {
        let k = self.k;
        let g = if k < self.stop {
            1.0
        } else if k - self.stop < self.fade {
            1.0 - (k - self.stop) as f64 / self.fade as f64
        } else {
            return None;
        };
        self.k += 1;
        Some((k, g * self.gain))
    }
}

/// Velocity to gain: vel^1.5, between a linear and a squared curve, so ghost notes sit low.
pub(super) fn velocity_gain(vel: f32) -> f64 {
    (vel as f64).clamp(0.0, 1.0).powf(1.5)
}

/// tanh saturation scaled so a full-scale input stays at full scale; none at drive 0.
pub(super) fn drive(x: f64, amount: f64) -> f64 {
    let g = 8.0 * amount.max(0.0);
    if g < 1e-6 {
        x
    } else {
        (g * x).tanh() / g.tanh()
    }
}

pub(super) fn add(out: &mut [f32; 2], y: f64) {
    out[0] += y as f32;
    out[1] += y as f32;
}

/// A frequency kept inside the band a filter or oscillator can hold at the rate.
pub(super) fn safe_hz(f: f64, rate: f64) -> f64 {
    f.clamp(1.0, 0.45 * rate)
}

#[derive(Clone, Copy, Default)]
pub(super) struct OnePole {
    a: f64,
    y: f64,
}

impl OnePole {
    pub(super) fn lowpass(f: f64, rate: f64) -> OnePole {
        OnePole {
            a: 1.0 - (-TAU * safe_hz(f, rate) / rate).exp(),
            y: 0.0,
        }
    }

    pub(super) fn tick(&mut self, x: f64) -> f64 {
        self.y += self.a * (x - self.y);
        self.y
    }
}

/// A second-order section from the audio EQ cookbook, direct form II transposed.
#[derive(Clone, Copy, Default)]
struct Biquad {
    b: [f64; 3],
    a: [f64; 2],
    z: [f64; 2],
}

impl Biquad {
    fn design(f: f64, q: f64, rate: f64, b: impl Fn(f64, f64) -> [f64; 3]) -> Biquad {
        let w = TAU * safe_hz(f, rate) / rate;
        let alpha = w.sin() / (2.0 * q);
        let a0 = 1.0 + alpha;
        let [b0, b1, b2] = b(w.cos(), alpha);
        Biquad {
            b: [b0 / a0, b1 / a0, b2 / a0],
            a: [-2.0 * w.cos() / a0, (1.0 - alpha) / a0],
            z: [0.0; 2],
        }
    }

    fn highpass(f: f64, q: f64, rate: f64) -> Biquad {
        Biquad::design(f, q, rate, |c, _| {
            [(1.0 + c) / 2.0, -(1.0 + c), (1.0 + c) / 2.0]
        })
    }

    /// Unity gain at the centre.
    fn bandpass(f: f64, q: f64, rate: f64) -> Biquad {
        Biquad::design(f, q, rate, |_, alpha| [alpha, 0.0, -alpha])
    }

    fn tick(&mut self, x: f64) -> f64 {
        let y = self.b[0] * x + self.z[0];
        self.z[0] = self.b[1] * x - self.a[0] * y + self.z[1];
        self.z[1] = self.b[2] * x - self.a[1] * y;
        y
    }
}

/// A square wave with its steps smoothed by PolyBLEP, so the partials above Nyquist fold
/// back far less than a plain one's.
#[derive(Clone, Copy, Default)]
struct Square {
    phase: f64,
    inc: f64,
}

fn blep(t: f64, dt: f64) -> f64 {
    if t < dt {
        let t = t / dt;
        2.0 * t - t * t - 1.0
    } else if t > 1.0 - dt {
        let t = (t - 1.0) / dt;
        t * t + 2.0 * t + 1.0
    } else {
        0.0
    }
}

impl Square {
    fn new(f: f64, rate: f64) -> Square {
        Square {
            phase: 0.0,
            inc: safe_hz(f, rate) / rate,
        }
    }

    fn tick(&mut self) -> f64 {
        let t = self.phase;
        let dt = self.inc;
        let naive = if t < 0.5 { 1.0 } else { -1.0 };
        let y = naive + blep(t, dt) - blep((t + 0.5).fract(), dt);
        self.phase = (t + dt).fract();
        y
    }
}

/// A sine from phase zero, its phase kept in cycles.
#[derive(Clone, Copy, Default)]
pub(super) struct Sine {
    phase: f64,
}

impl Sine {
    pub(super) fn tick(&mut self, f: f64, rate: f64) -> f64 {
        let y = (TAU * self.phase).sin();
        self.phase = (self.phase + safe_hz(f, rate) / rate).fract();
        y
    }
}

/// The frequency ratio a step's pitch puts on a drum: its note is 60 plus the semitones.
fn pitch_ratio(e: &Event) -> f64 {
    ((e.note as f64 - 60.0) / 12.0).exp2()
}

/// The decay a step's scale leaves of `seconds`.
fn scaled(seconds: f32, e: &Event) -> f64 {
    seconds as f64 * (e.decay as f64).max(0.0)
}

/// What every drum voice keeps: its rate, its instrument's longest life and the gate.
#[derive(Clone, Copy, Default)]
struct Base {
    rate: f64,
    max_life_s: f64,
    gate: Gate,
    id: u32,
}

impl Base {
    fn new(name: &str, rate: f32) -> Base {
        Base {
            rate: rate as f64,
            max_life_s: instrument(name).map_or(0.0, |i| i.max_life_s as f64),
            ..Base::default()
        }
    }

    fn start(&mut self, e: &Event) {
        let life = (self.max_life_s * (e.decay as f64).max(0.0) * self.rate).round() as u64;
        self.gate = Gate::new(e, self.rate, Some(life));
        self.id = e.id;
    }

    fn noise(&self, k: u64) -> f64 {
        noise(self.id, k) as f64
    }
}

mod kick {
    pub const TUNE: usize = 0;
    pub const PITCH_DROP: usize = 1;
    pub const DROP_MS: usize = 2;
    pub const DECAY: usize = 3;
    pub const CLICK: usize = 4;
    pub const DRIVE: usize = 5;
}

/// The click's length: a beater, not a tone.
const CLICK_S: f64 = 0.008;

/// A sine falling in pitch from `pitch_drop` above its tune, a noise click and a drive.
#[derive(Default)]
struct Kick {
    base: Base,
    sine: Sine,
    tune: f64,
    /// semitones above the tune, falling by `fall` a frame
    above: f64,
    fall: f64,
    env: Fall,
    click_env: Fall,
    click: f64,
    drive: f64,
}

impl Voice for Kick {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.sine = Sine::default();
        self.tune = p[kick::TUNE] as f64 * pitch_ratio(e);
        self.above = p[kick::PITCH_DROP] as f64;
        let tau = (p[kick::DROP_MS] as f64 / 1000.0 * rate).max(1.0);
        self.fall = (-1.0 / tau).exp();
        self.env = Fall::new(scaled(p[kick::DECAY], e), rate);
        self.click_env = Fall::new(CLICK_S, rate);
        self.click = p[kick::CLICK] as f64;
        self.drive = p[kick::DRIVE] as f64;
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        let rate = self.base.rate;
        for o in out.iter_mut() {
            if self.env.done() && self.click_env.done() {
                return false;
            }
            let Some((k, g)) = self.base.gate.next() else {
                return false;
            };
            let f = self.tune * (self.above / 12.0).exp2();
            self.above *= self.fall;
            let body = self.sine.tick(f, rate) * self.env.next();
            let click = self.base.noise(k) * self.click_env.next() * self.click;
            add(o, drive(body + click, self.drive) * g);
        }
        true
    }
}

mod snare {
    pub const TUNE: usize = 0;
    pub const DECAY: usize = 1;
    pub const TONE: usize = 2;
    pub const SNAP: usize = 3;
}

/// Two shell modes, the upper one quieter, and high-passed noise.
#[derive(Default)]
struct Snare {
    base: Base,
    low: Sine,
    high: Sine,
    f: f64,
    body_env: Fall,
    noise_env: Fall,
    hp: Biquad,
    body: f64,
    snares: f64,
}

impl Voice for Snare {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.low = Sine::default();
        self.high = Sine::default();
        self.f = p[snare::TUNE] as f64 * pitch_ratio(e);
        let decay = scaled(p[snare::DECAY], e);
        // the shell dies well before the wires
        self.body_env = Fall::new(0.5 * decay, rate);
        self.noise_env = Fall::new(decay, rate);
        let snap = p[snare::SNAP] as f64;
        self.hp = Biquad::highpass(1000.0 + 4000.0 * snap, 0.707, rate);
        let tone = p[snare::TONE] as f64;
        self.body = 1.0 - tone;
        self.snares = tone * (0.6 + 0.8 * snap);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        let rate = self.base.rate;
        for o in out.iter_mut() {
            if self.body_env.done() && self.noise_env.done() {
                return false;
            }
            let Some((k, g)) = self.base.gate.next() else {
                return false;
            };
            let shell =
                (self.low.tick(self.f, rate) + 0.6 * self.high.tick(1.5 * self.f, rate)) / 1.6;
            let wires = self.hp.tick(self.base.noise(k));
            let y = self.body * shell * self.body_env.next()
                + self.snares * wires * self.noise_env.next();
            add(o, y * g);
        }
        true
    }
}

mod clap {
    pub const DECAY: usize = 0;
    pub const SPREAD_MS: usize = 1;
    pub const TONE: usize = 2;
}

/// Hands before the tail.
const CLAP_BURSTS: u64 = 3;

/// Three noise bursts a third of the spread apart, then a tail, all through a band-pass.
#[derive(Default)]
struct Clap {
    base: Base,
    bp: Biquad,
    /// frames between bursts
    gap: u64,
    tail: Fall,
}

impl Voice for Clap {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.bp = Biquad::bandpass(p[clap::TONE] as f64 * pitch_ratio(e), 1.5, rate);
        self.gap = ((p[clap::SPREAD_MS] as f64 / 3000.0 * rate).round() as u64).max(1);
        self.tail = Fall::new(scaled(p[clap::DECAY], e), rate);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        let bursts = CLAP_BURSTS * self.gap;
        for o in out.iter_mut() {
            let Some((k, g)) = self.base.gate.next() else {
                return false;
            };
            let env = if k < bursts {
                let t = (k % self.gap) as f64 / self.gap as f64;
                (FLOOR.powf(t) - FLOOR) / (1.0 - FLOOR)
            } else if self.tail.done() {
                return false;
            } else {
                0.8 * self.tail.next()
            };
            // the band-pass takes a third of the noise's level; this brings it back
            let y = 2.0 * self.bp.tick(self.base.noise(k)) * env;
            add(o, y * g);
        }
        true
    }
}

mod hat {
    pub const DECAY: usize = 0;
    pub const TONE: usize = 1;
    pub const METAL: usize = 2;
}

/// The six square oscillators of the classic hat, in Hz (a forum figure, not measured).
const HAT_SQUARES: [f64; 6] = [205.3, 304.4, 369.6, 522.7, 540.0, 800.0];

/// The square bank and noise in a blend, high-passed at `tone`; open and closed differ in
/// their params only.
#[derive(Default)]
struct Hat {
    base: Base,
    squares: [Square; 6],
    hp: [Biquad; 2],
    env: Fall,
    metal: f64,
}

impl Voice for Hat {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        let r = pitch_ratio(e);
        for (s, f) in self.squares.iter_mut().zip(HAT_SQUARES) {
            *s = Square::new(f * r, rate);
        }
        let tone = p[hat::TONE] as f64 * r;
        self.hp = [
            Biquad::highpass(tone, 0.54, rate),
            Biquad::highpass(tone, 1.31, rate),
        ];
        self.env = Fall::new(scaled(p[hat::DECAY], e), rate);
        self.metal = (p[hat::METAL] as f64).clamp(0.0, 1.0);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        for o in out.iter_mut() {
            if self.env.done() {
                return false;
            }
            let Some((k, g)) = self.base.gate.next() else {
                return false;
            };
            // the bank's partials that pass the high-pass are far weaker than the noise's
            let bank: f64 = self.squares.iter_mut().map(|s| s.tick()).sum::<f64>() * 0.25;
            let x = self.metal * bank + (1.0 - self.metal) * self.base.noise(k);
            let y = self.hp[0].tick(x);
            let y = self.hp[1].tick(y);
            add(o, y * self.env.next() * g);
        }
        true
    }
}

mod rim {
    pub const TUNE: usize = 0;
    pub const DECAY: usize = 1;
}

/// A short sine at the tune and a shorter crack of high-passed noise.
#[derive(Default)]
struct Rim {
    base: Base,
    sine: Sine,
    f: f64,
    env: Fall,
    crack_env: Fall,
    hp: Biquad,
}

impl Voice for Rim {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.sine = Sine::default();
        self.f = p[rim::TUNE] as f64 * pitch_ratio(e);
        let decay = scaled(p[rim::DECAY], e);
        self.env = Fall::new(decay, rate);
        self.crack_env = Fall::new(0.3 * decay, rate);
        self.hp = Biquad::highpass(2000.0, 0.707, rate);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        let rate = self.base.rate;
        for o in out.iter_mut() {
            if self.env.done() {
                return false;
            }
            let Some((k, g)) = self.base.gate.next() else {
                return false;
            };
            let crack = self.hp.tick(self.base.noise(k)) * self.crack_env.next();
            let y = self.sine.tick(self.f, rate) * self.env.next() + 0.4 * crack;
            add(o, y * g);
        }
        true
    }
}

mod cowbell {
    pub const TUNE: usize = 0;
    pub const DECAY: usize = 1;
}

/// The upper square's ratio to the lower.
const COWBELL_RATIO: f64 = 1.48;

/// Two squares through a band-pass, a sharp first fall over a longer tail.
#[derive(Default)]
struct Cowbell {
    base: Base,
    low: Square,
    high: Square,
    bp: Biquad,
    strike: Fall,
    ring: Fall,
}

impl Voice for Cowbell {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        let f = p[cowbell::TUNE] as f64 * pitch_ratio(e);
        self.low = Square::new(f, rate);
        self.high = Square::new(f * COWBELL_RATIO, rate);
        self.bp = Biquad::bandpass(f * COWBELL_RATIO.sqrt(), 1.2, rate);
        let decay = scaled(p[cowbell::DECAY], e);
        self.strike = Fall::new(0.15 * decay, rate);
        self.ring = Fall::new(decay, rate);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        for o in out.iter_mut() {
            if self.ring.done() {
                return false;
            }
            let Some((_, g)) = self.base.gate.next() else {
                return false;
            };
            let x = 0.5 * (self.low.tick() + self.high.tick());
            let env = 0.6 * self.strike.next() + 0.4 * self.ring.next();
            add(o, self.bp.tick(x) * env * g);
        }
        true
    }
}

pub fn make(instrument: &str, rate: f32) -> Option<Box<dyn Voice>> {
    let base = Base::new(instrument, rate);
    let voice: Box<dyn Voice> = match instrument {
        "kick" => Box::new(Kick {
            base,
            ..Kick::default()
        }),
        "snare" => Box::new(Snare {
            base,
            ..Snare::default()
        }),
        "clap" => Box::new(Clap {
            base,
            ..Clap::default()
        }),
        "hat_closed" | "hat_open" => Box::new(Hat {
            base,
            ..Hat::default()
        }),
        "rim" => Box::new(Rim {
            base,
            ..Rim::default()
        }),
        "cowbell" => Box::new(Cowbell {
            base,
            ..Cowbell::default()
        }),
        _ => return None,
    };
    Some(voice)
}
