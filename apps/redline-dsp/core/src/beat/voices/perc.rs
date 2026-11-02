//! The extra drum voices: tom, shaker, crash, snap and a struck block, built like the drum
//! machine's: every envelope reaches exactly zero at its length, and a hit's life is bounded
//! by its instrument's.

use super::super::events::Event;
use super::drums::{add, drive, pitch_ratio, scaled, Base, Fall, OnePole, Sine};
use super::osc::Svf;
use super::{Ctx, Voice};

mod tom {
    pub const TUNE: usize = 0;
    pub const PITCH_DROP: usize = 1;
    pub const DECAY: usize = 2;
    pub const DRIVE: usize = 3;
}

/// The drop's time constant: within 2% of the drop after 60 ms.
const TOM_DROP_S: f64 = 0.015;
const TOM_CLICK_S: f64 = 0.004;
const TOM_CLICK: f64 = 0.15;
/// Softens the click into a stick on a head rather than a tick.
const TOM_CLICK_HZ: f64 = 3000.0;

/// A sine falling to its tune from `pitch_drop` above, a soft noise click and a drive.
#[derive(Default)]
struct Tom {
    base: Base,
    sine: Sine,
    tune: f64,
    /// semitones above the tune, falling by `fall` a frame
    above: f64,
    fall: f64,
    env: Fall,
    click_env: Fall,
    lp: OnePole,
    drive: f64,
}

impl Voice for Tom {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.sine = Sine::default();
        self.tune = p[tom::TUNE] as f64 * pitch_ratio(e);
        self.above = p[tom::PITCH_DROP] as f64;
        self.fall = (-1.0 / (TOM_DROP_S * rate)).exp();
        self.env = Fall::new(scaled(p[tom::DECAY], e), rate);
        self.click_env = Fall::new(TOM_CLICK_S, rate);
        self.lp = OnePole::lowpass(TOM_CLICK_HZ, rate);
        self.drive = p[tom::DRIVE] as f64;
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
            let click = self.lp.tick(self.base.noise(k)) * self.click_env.next() * TOM_CLICK;
            add(o, drive(body + click, self.drive) * g);
        }
        true
    }
}

mod shaker {
    pub const DECAY: usize = 0;
    pub const TONE: usize = 1;
    pub const ATTACK_MS: usize = 2;
}

/// Band-passed noise rising over `attack_ms`, then falling over `decay`.
#[derive(Default)]
struct Shaker {
    base: Base,
    bp: Svf,
    /// frames of the rise
    attack: u64,
    env: Fall,
}

impl Voice for Shaker {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.bp = Svf::new(p[shaker::TONE] as f64 * pitch_ratio(e), 1.2, rate);
        self.attack = ((p[shaker::ATTACK_MS] as f64 / 1000.0 * rate).round() as u64).max(1);
        self.env = Fall::new(scaled(p[shaker::DECAY], e), rate);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        for o in out.iter_mut() {
            let Some((k, g)) = self.base.gate.next() else {
                return false;
            };
            let env = if k < self.attack {
                k as f64 / self.attack as f64
            } else if self.env.done() {
                return false;
            } else {
                self.env.next()
            };
            // the band-pass keeps a fraction of white noise's level; this brings it back
            let y = 1.2 * self.bp.tick(self.base.noise(k)).band * env;
            add(o, y * g);
        }
        true
    }
}

mod crash {
    pub const DECAY: usize = 0;
    pub const TONE: usize = 1;
    pub const METAL: usize = 2;
}

/// The crash's partials in Hz, spread unevenly so no pitch forms.
const CRASH_PARTIALS: [f64; 6] = [263.0, 391.0, 427.0, 588.0, 707.0, 943.0];
/// Pushes each sine near a square: the odd harmonics a cymbal's shimmer is made of, without
/// a hard edge to alias.
const CRASH_SQUARE: f64 = 0.5;

/// Near-square partials and noise in a blend, high-passed at `tone`, a bright strike over a
/// long ring.
#[derive(Default)]
struct Crash {
    base: Base,
    partials: [Sine; 6],
    freqs: [f64; 6],
    hp: [Svf; 2],
    strike: Fall,
    ring: Fall,
    metal: f64,
}

impl Voice for Crash {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        let r = pitch_ratio(e);
        self.partials = [Sine::default(); 6];
        for (f, base) in self.freqs.iter_mut().zip(CRASH_PARTIALS) {
            *f = base * r;
        }
        let tone = p[crash::TONE] as f64 * r;
        self.hp = [Svf::new(tone, 0.54, rate), Svf::new(tone, 1.31, rate)];
        let decay = scaled(p[crash::DECAY], e);
        self.strike = Fall::new(0.1 * decay, rate);
        self.ring = Fall::new(decay, rate);
        self.metal = (p[crash::METAL] as f64).clamp(0.0, 1.0);
    }

    fn render(&mut self, out: &mut [[f32; 2]], _ctx: &Ctx) -> bool {
        let rate = self.base.rate;
        for o in out.iter_mut() {
            if self.ring.done() {
                return false;
            }
            let Some((k, g)) = self.base.gate.next() else {
                return false;
            };
            let bank: f64 = self
                .partials
                .iter_mut()
                .zip(self.freqs)
                .map(|(s, f)| drive(s.tick(f, rate), CRASH_SQUARE))
                .sum::<f64>()
                * 0.25;
            let x = self.metal * bank + (1.0 - self.metal) * self.base.noise(k);
            let y = self.hp[0].tick(x).high;
            let y = self.hp[1].tick(y).high;
            let env = 0.4 * self.strike.next() + 0.6 * self.ring.next();
            add(o, y * env * g);
        }
        true
    }
}

mod snap {
    pub const DECAY: usize = 0;
    pub const TONE: usize = 1;
}

/// The click's longest length.
const SNAP_CLICK_S: f64 = 0.006;
const SNAP_CLICK: f64 = 0.5;

/// A burst of noise band-passed at `tone` and a short ping on it.
#[derive(Default)]
struct Snap {
    base: Base,
    bp: Svf,
    click: Sine,
    f: f64,
    env: Fall,
    click_env: Fall,
}

impl Voice for Snap {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.f = p[snap::TONE] as f64 * pitch_ratio(e);
        self.bp = Svf::new(self.f, 1.5, rate);
        self.click = Sine::default();
        let decay = scaled(p[snap::DECAY], e);
        self.env = Fall::new(decay, rate);
        self.click_env = Fall::new(SNAP_CLICK_S.min(decay), rate);
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
            let burst = 2.0 * self.bp.tick(self.base.noise(k)).band * self.env.next();
            let click = self.click.tick(self.f, rate) * self.click_env.next() * SNAP_CLICK;
            add(o, (burst + click) * g);
        }
        true
    }
}

mod block {
    pub const TUNE: usize = 0;
    pub const DECAY: usize = 1;
    pub const METAL: usize = 2;
}

/// The overtones `metal` brings in, over the tune: off the harmonic series, as a struck
/// plate's are.
const PERC_PARTIALS: [f64; 3] = [1.47, 2.37, 3.73];
const PERC_CLICK_S: f64 = 0.002;
const PERC_CLICK: f64 = 0.2;

/// A decaying sine at the tune, inharmonic partials mixed in by `metal` and a strike's tick.
#[derive(Default)]
struct Perc {
    base: Base,
    ping: Sine,
    partials: [Sine; 3],
    f: f64,
    env: Fall,
    click_env: Fall,
    metal: f64,
}

impl Voice for Perc {
    fn start(&mut self, e: &Event, p: &[f32], _ctx: &Ctx) {
        let rate = self.base.rate;
        self.base.start(e);
        self.ping = Sine::default();
        self.partials = [Sine::default(); 3];
        self.f = p[block::TUNE] as f64 * pitch_ratio(e);
        let decay = scaled(p[block::DECAY], e);
        self.env = Fall::new(decay, rate);
        self.click_env = Fall::new(PERC_CLICK_S.min(decay), rate);
        self.metal = (p[block::METAL] as f64).clamp(0.0, 1.0);
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
            let f = self.f;
            let partials: f64 = self
                .partials
                .iter_mut()
                .zip(PERC_PARTIALS)
                .map(|(s, ratio)| s.tick(f * ratio, rate))
                .sum::<f64>()
                / PERC_PARTIALS.len() as f64;
            let tone = (self.ping.tick(f, rate) + self.metal * partials) / (1.0 + self.metal);
            let click = self.base.noise(k) * self.click_env.next() * PERC_CLICK;
            add(o, (tone * self.env.next() + click) * g);
        }
        true
    }
}

pub fn make(instrument: &str, rate: f32) -> Option<Box<dyn Voice>> {
    let base = Base::new(instrument, rate);
    let voice: Box<dyn Voice> = match instrument {
        "tom" => Box::new(Tom {
            base,
            ..Tom::default()
        }),
        "shaker" => Box::new(Shaker {
            base,
            ..Shaker::default()
        }),
        "crash" => Box::new(Crash {
            base,
            ..Crash::default()
        }),
        "snap" => Box::new(Snap {
            base,
            ..Snap::default()
        }),
        "perc" => Box::new(Perc {
            base,
            ..Perc::default()
        }),
        _ => return None,
    };
    Some(voice)
}
