//! The stages of redline's render that the other effects do not cover, streaming, so the whole
//! engine (vocal chain, beat, master) can run as racks.
//!
//! - chain_compressor: `redline.core.dsp.compressor_gain_db`. The peak of the louder channel per
//!   1 ms block, the median of five blocks around each, a compressor over `threshold` and a
//!   1:2 expander under `gate` (at most 40 dB down), smoothed per block, read linearly between
//!   block centres. The median looks two blocks ahead and the reading one more, so the effect
//!   runs four blocks late, as `lookahead` has it by default. A shorter lookahead (0 for a
//!   live monitor) reads the newest gain known for the blocks it cannot see yet.
//!   `makeup` is the loudness the offline chain gives back afterwards.
//! - chain_clip: the drive and clip of `redline.vocal.chain.render_dirt` at eight times the
//!   rate: tanh after `drive` dB, then a hard clip `below` dB under the driven level of the loud
//!   part of the voice, `peak` (measured offline, before the drive).
//! - bass_drive: `redline.master.clip.saturate_low`. A Linkwitz-Riley split at `freq`, the band
//!   under it through tanh at twice the rate, its level under 100 Hz brought back to the
//!   band's own (over the last seconds rather than the whole file), the rest added back late
//!   by as much as the oversampling. No drive passes the input through, late by as much.
//! - master: `redline.master.clip._clip_clean` streaming. `gain`, then at eight times the rate
//!   a clip at `ceiling` and a Butterworth band limit at 20 kHz twice, a last clip, back down,
//!   then `trim`. Offline the band limit runs forwards and backwards and the trim is the
//!   whole file's overshoot; here the filter is causal and a clip at the ceiling catches what
//!   still rings over it.
//!
//! Oversampling uses `scipy.signal.resample_poly`'s own filter (a Kaiser windowed sinc, beta
//! 10, ten taps per phase each side), so up and down cost 20 frames together at any factor.

use crate::design::{self, Band as Pass};
use crate::dynamics::Sec;
use crate::effect::{check, finite, Effect, ParamError, ParamSpec, Smoothed};
use std::f64::consts::{FRAC_1_SQRT_2, PI};

const DEFAULT_RATE: f32 = 44100.0;
const ON_OFF: &[&str] = &["off", "on"];
const BLOCK_SECONDS: f64 = 0.001;
const LEVEL_FLOOR: f32 = 1e-6;
const EXPANDER_RANGE_DB: f64 = 40.0;
/// Blocks the compressor needs to read its gain as the offline law does.
const LOOKAHEAD_BLOCKS: usize = 4;
/// Taps each side of a polyphase filter, per unit of the factor, as resample_poly designs it.
const HALF_PER_FACTOR: usize = 10;
/// Frames the up- and down-sampling filters are late by together, at any factor.
const OVERSAMPLING_LATENCY: usize = 2 * HALF_PER_FACTOR;
const KAISER_BETA: f64 = 10.0;
const MAX_FACTOR: usize = 8;
/// Frames between redesigns of the crossover while its frequency glides.
const REDESIGN: usize = 32;
const SUB_HZ: f64 = 100.0;
/// The time constant of the sub level bass_drive keeps, in seconds: long enough not to pump
/// with each hit, short enough to follow a new drive within a bar or two.
const SUB_SECONDS: f32 = 3.0;
const MASTER_FACTOR: usize = 8;
const BAND_LIMIT_HZ: f64 = 20000.0;
const CLIP_PASSES: usize = 2;
const BUTTERWORTH_ORDER: usize = 8;
const SECTIONS: usize = BUTTERWORTH_ORDER / 2;

fn amplitude(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

/// A rate a filter can be designed at; a host refuses the others before they get here.
fn usable_rate(rate: f32) -> f32 {
    if rate.is_finite() && rate > 0.0 {
        rate
    } else {
        DEFAULT_RATE
    }
}

fn bessel_i0(x: f64) -> f64 {
    let (mut sum, mut term) = (1.0, 1.0);
    let q = x * x / 4.0;
    for k in 1..500 {
        term *= q / (k * k) as f64;
        sum += term;
        if term < sum * 1e-17 {
            break;
        }
    }
    sum
}

/// `firwin(2 * half + 1, 1 / factor, window=("kaiser", 10))`, the taps summing to `gain`.
fn poly_taps(factor: usize, gain: f64) -> Vec<f32> {
    let half = HALF_PER_FACTOR * factor;
    let n = 2 * half + 1;
    let cutoff = 1.0 / factor as f64;
    let h: Vec<f64> = (0..n)
        .map(|i| {
            let m = i as f64 - half as f64;
            let sinc = if m == 0.0 {
                1.0
            } else {
                (PI * cutoff * m).sin() / (PI * cutoff * m)
            };
            let r = 2.0 * i as f64 / (n - 1) as f64 - 1.0;
            let window =
                bessel_i0(KAISER_BETA * (1.0 - r * r).max(0.0).sqrt()) / bessel_i0(KAISER_BETA);
            cutoff * sinc * window
        })
        .collect();
    let sum: f64 = h.iter().sum();
    h.iter().map(|v| (v / sum * gain) as f32).collect()
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

/// The recent past of one signal, oldest first, as one slice: each value is written twice,
/// `len` apart.
struct History {
    buf: Vec<f32>,
    len: usize,
    pos: usize,
}

impl History {
    fn new(len: usize) -> Self {
        History {
            buf: vec![0.0; 2 * len],
            len,
            pos: 0,
        }
    }

    fn clear(&mut self) {
        self.buf.fill(0.0);
        self.pos = 0;
    }

    #[inline]
    fn push(&mut self, v: f32) {
        self.pos = if self.pos + 1 == self.len {
            0
        } else {
            self.pos + 1
        };
        self.buf[self.pos] = v;
        self.buf[self.pos + self.len] = v;
    }

    /// The last `len` values, oldest first.
    #[inline]
    fn window(&self) -> &[f32] {
        &self.buf[self.pos + 1..=self.pos + self.len]
    }
}

/// Streaming polyphase resampling up by a factor, a stage at the raised rate, and back down,
/// for two channels. The output frame `k` holds the input from frame `k - 20`.
struct Oversampler {
    factor: usize,
    /// Phase p's taps, oldest input first, at `p * per_phase`.
    phases: Vec<f32>,
    per_phase: usize,
    down: Vec<f32>,
    up_past: [History; 2],
    down_past: [History; 2],
}

impl Oversampler {
    fn new(factor: usize) -> Self {
        let up = poly_taps(factor, factor as f64);
        let per_phase = up.len().div_ceil(factor);
        let mut phases = vec![0.0; factor * per_phase];
        for p in 0..factor {
            for j in 0..per_phase {
                // phase p's tap for the input j frames back
                let tap = up.get(p + j * factor).copied().unwrap_or(0.0);
                phases[p * per_phase + per_phase - 1 - j] = tap;
            }
        }
        let mut down = poly_taps(factor, 1.0);
        down.reverse();
        let taps = down.len();
        Oversampler {
            factor,
            phases,
            per_phase,
            down,
            up_past: [History::new(per_phase), History::new(per_phase)],
            down_past: [History::new(taps), History::new(taps)],
        }
    }

    fn clear(&mut self) {
        for h in self.up_past.iter_mut().chain(&mut self.down_past) {
            h.clear();
        }
    }

    /// One frame of channel `ch`: `stage` sees each sample at the raised rate in turn.
    #[inline]
    fn tick(&mut self, ch: usize, x: f32, mut stage: impl FnMut(f32) -> f32) -> f32 {
        self.up_past[ch].push(x);
        let mut out = 0.0;
        for p in 0..self.factor {
            let taps = &self.phases[p * self.per_phase..(p + 1) * self.per_phase];
            let v = stage(dot(taps, self.up_past[ch].window()));
            self.down_past[ch].push(v);
            // the kept sample of the decimation is the one in step with the input frame
            if p == 0 {
                out = dot(&self.down, self.down_past[ch].window());
            }
        }
        out
    }
}

pub struct ChainCompressor {
    threshold: f64,
    ratio: f64,
    attack_ms: f64,
    release_ms: f64,
    gate: f64,
    makeup: Smoothed,
    rate: f32,
    block: usize,
    attack: f64,
    release: f64,
    peak: f32,
    filled: usize,
    blocks: usize,
    levels: [f64; 5],
    pushed: usize,
    smoothed: [f64; 8],
    squash: f64,
    expand: f64,
    lookahead: usize,
    delay: Vec<[f32; 2]>,
    // u64: a usize is 32 bits on wasm32 and would wrap within a day of play
    now: u64,
}

impl ChainCompressor {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("threshold", -60.0, 0.0, -38.0, "dB"),
        ParamSpec::range("ratio", 1.0, 40.0, 30.0, ":1"),
        ParamSpec::range("attack", 0.1, 50.0, 1.0, "ms"),
        ParamSpec::range("release", 5.0, 500.0, 40.0, "ms"),
        ParamSpec::range("gate", -120.0, 0.0, -120.0, "dB"),
        ParamSpec::range("makeup", -24.0, 48.0, 0.0, "dB"),
        // in 1 ms blocks
        ParamSpec::range(
            "lookahead",
            0.0,
            LOOKAHEAD_BLOCKS as f32,
            LOOKAHEAD_BLOCKS as f32,
            "ms",
        )
        .fixed(),
    ];

    pub fn new() -> Self {
        let mut c = ChainCompressor {
            threshold: -38.0,
            ratio: 30.0,
            attack_ms: 1.0,
            release_ms: 40.0,
            gate: -120.0,
            makeup: Smoothed::new(1.0),
            rate: DEFAULT_RATE,
            block: 1,
            attack: 0.0,
            release: 0.0,
            peak: 0.0,
            filled: 0,
            blocks: 0,
            levels: [0.0; 5],
            pushed: 0,
            smoothed: [0.0; 8],
            squash: 0.0,
            expand: 0.0,
            lookahead: LOOKAHEAD_BLOCKS,
            delay: Vec::new(),
            now: 0,
        };
        c.times();
        c
    }

    fn times(&mut self) {
        self.block = ((self.rate as f64 * BLOCK_SECONDS).round() as usize).max(1);
        let step = self.block as f64 / self.rate as f64;
        self.attack = (-step / (self.attack_ms / 1000.0)).exp();
        self.release = (-step / (self.release_ms / 1000.0)).exp();
    }

    fn push_level(&mut self, level: f64) {
        self.levels[self.pushed % 5] = level;
        self.pushed += 1;
    }

    /// A block is complete: its level joins the median, which gives the gain of the block
    /// two before it.
    fn end_block(&mut self) {
        let level = 20.0 * (self.peak.max(LEVEL_FLOOR) as f64).log10();
        self.peak = 0.0;
        self.filled = 0;
        if self.blocks == 0 {
            // the law pads the first block's level twice before it
            for _ in 0..3 {
                self.push_level(level);
            }
        } else {
            self.push_level(level);
        }
        self.blocks += 1;
        if self.pushed < 5 {
            return;
        }
        let mut sorted = self.levels;
        sorted.sort_unstable_by(f64::total_cmp);
        let median = sorted[2];
        let aim_squash = if median > self.threshold {
            (self.threshold - median) * (1.0 - 1.0 / self.ratio)
        } else {
            0.0
        };
        let aim_expand = if median < self.gate {
            (median - self.gate).max(-EXPANDER_RANGE_DB)
        } else {
            0.0
        };
        let c = if aim_squash < self.squash {
            self.attack
        } else {
            self.release
        };
        self.squash = c * self.squash + (1.0 - c) * aim_squash;
        let c = if aim_expand > self.expand {
            self.attack
        } else {
            self.release
        };
        self.expand = c * self.expand + (1.0 - c) * aim_expand;
        self.smoothed[(self.blocks - 3) & 7] = self.squash + self.expand;
    }

    /// The gain in dB of frame `n`, between the centres of the blocks around it, read at
    /// frame `now`: a block whose gain is not known yet takes the newest one that is.
    fn gain_db(&self, n: u64, now: u64) -> f64 {
        let b = self.block as f64;
        let pos = n as f64 - (b - 1.0) / 2.0;
        if pos <= 0.0 {
            return self.smoothed[0];
        }
        let i = (pos / b).floor() as u64;
        let t = (pos - i as f64 * b) / b;
        // a block's gain is known two blocks after it ends; with the full lookahead, always
        let known = ((now + 1) / self.block as u64).saturating_sub(3);
        let (a, z) = (
            self.smoothed[(i.min(known) & 7) as usize],
            self.smoothed[((i + 1).min(known) & 7) as usize],
        );
        a + t * (z - a)
    }
}

impl Effect for ChainCompressor {
    fn name(&self) -> &'static str {
        "chain_compressor"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let v = value as f64;
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.threshold = v,
            1 => self.ratio = v,
            2 => self.attack_ms = v,
            3 => self.release_ms = v,
            4 => self.gate = v,
            5 => self.makeup.set(amplitude(value)),
            _ => self.lookahead = value.round() as usize,
        }
        // the block length depends on the rate only, which set does not change
        let step = self.block as f64 / self.rate as f64;
        self.attack = (-step / (self.attack_ms / 1000.0)).exp();
        self.release = (-step / (self.release_ms / 1000.0)).exp();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = usable_rate(rate);
        self.times();
        self.makeup.reset(self.rate);
        self.peak = 0.0;
        self.filled = 0;
        self.blocks = 0;
        self.pushed = 0;
        self.levels = [0.0; 5];
        self.smoothed = [0.0; 8];
        self.squash = 0.0;
        self.expand = 0.0;
        self.delay.clear();
        self.delay.resize(
            (self.lookahead * self.block + 1).next_power_of_two(),
            [0.0; 2],
        );
        self.now = 0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.delay.is_empty() {
            return;
        }
        finite(block);
        let mask = self.delay.len() - 1;
        let lag = (self.lookahead * self.block) as u64;
        for f in block {
            self.peak = self.peak.max(f[0].abs()).max(f[1].abs());
            self.delay[self.now as usize & mask] = *f;
            self.filled += 1;
            if self.filled == self.block {
                self.end_block();
            }
            let makeup = self.makeup.next();
            *f = if self.now >= lag {
                let n = self.now - lag;
                let g = (10f64.powf(self.gain_db(n, self.now) / 20.0)) as f32 * makeup;
                let x = self.delay[n as usize & mask];
                [x[0] * g, x[1] * g]
            } else {
                [0.0; 2]
            };
            self.now += 1;
        }
    }
    fn latency(&self) -> usize {
        self.lookahead * self.block
    }
}

pub struct ChainClip {
    drive_db: f32,
    below_db: f32,
    peak_db: f32,
    clip: bool,
    drive: Smoothed,
    ceiling: Smoothed,
    over: Oversampler,
}

impl ChainClip {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("drive", 0.0, 48.0, 30.0, "dB"),
        ParamSpec::choice("clip", ON_OFF),
        ParamSpec::range("below", 0.0, 48.0, 12.0, "dB"),
        ParamSpec::range("peak", -60.0, 12.0, 0.0, "dBFS"),
    ];

    pub fn new() -> Self {
        let mut c = ChainClip {
            drive_db: 30.0,
            below_db: 12.0,
            peak_db: 0.0,
            clip: false,
            drive: Smoothed::new(1.0),
            ceiling: Smoothed::new(1.0),
            over: Oversampler::new(MAX_FACTOR),
        };
        c.apply();
        c
    }

    fn apply(&mut self) {
        let drive = amplitude(self.drive_db);
        self.drive.set(drive);
        // tanh never reaches 1, so a ceiling of 1 is no clip at all
        self.ceiling.set(if self.clip {
            (amplitude(self.peak_db) * drive).tanh() * amplitude(-self.below_db)
        } else {
            1.0
        });
    }
}

impl Effect for ChainClip {
    fn name(&self) -> &'static str {
        "chain_clip"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.drive_db = value,
            1 => self.clip = value.round() >= 1.0,
            2 => self.below_db = value,
            _ => self.peak_db = value,
        }
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        let rate = usable_rate(rate);
        self.drive.reset(rate);
        self.ceiling.reset(rate);
        self.over.clear();
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let (drive, ceiling) = (self.drive.next(), self.ceiling.next());
            for (ch, v) in f.iter_mut().enumerate() {
                *v = self
                    .over
                    .tick(ch, *v, |u| (u * drive).tanh().clamp(-ceiling, ceiling));
            }
        }
    }
    fn latency(&self) -> usize {
        OVERSAMPLING_LATENCY
    }
}

pub struct BassDrive {
    freq: Smoothed,
    drive: Smoothed,
    wet: Smoothed,
    rate: f32,
    countdown: usize,
    low: [[Sec; 2]; 2],
    high: [[Sec; 2]; 2],
    sub_before: [[Sec; SECTIONS]; 2],
    sub_after: [[Sec; SECTIONS]; 2],
    power_before: f64,
    power_after: f64,
    keep: f64,
    level: f32,
    over: Oversampler,
    past: [[[f32; 2]; 2]; OVERSAMPLING_LATENCY],
    pos: usize,
}

impl BassDrive {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("freq", 20.0, 500.0, 120.0, "Hz"),
        ParamSpec::range("drive", 0.0, 48.0, 12.0, "dB"),
    ];
    const FACTOR: usize = 2;

    pub fn new() -> Self {
        let mut b = BassDrive {
            freq: Smoothed::new(120f32.log2()),
            drive: Smoothed::new(amplitude(12.0)),
            wet: Smoothed::new(1.0),
            rate: DEFAULT_RATE,
            countdown: 0,
            low: [[Sec::new(); 2]; 2],
            high: [[Sec::new(); 2]; 2],
            sub_before: [[Sec::new(); SECTIONS]; 2],
            sub_after: [[Sec::new(); SECTIONS]; 2],
            power_before: 0.0,
            power_after: 0.0,
            keep: 0.0,
            level: 1.0,
            over: Oversampler::new(Self::FACTOR),
            past: [[[0.0; 2]; 2]; OVERSAMPLING_LATENCY],
            pos: 0,
        };
        b.redesign();
        b
    }

    fn redesign(&mut self) {
        let freq = (self.freq.value().exp2() as f64).min(0.45 * self.rate as f64);
        let rate = self.rate as f64;
        // a second-order Butterworth is the cookbook's pass at q 1/sqrt(2), twice is LR4
        let (lp, hp) = (
            design::lowpass(freq, FRAC_1_SQRT_2, rate),
            design::highpass(freq, FRAC_1_SQRT_2, rate),
        );
        for ch in 0..2 {
            for s in &mut self.low[ch] {
                s.set(lp);
            }
            for s in &mut self.high[ch] {
                s.set(hp);
            }
        }
    }
}

impl Effect for BassDrive {
    fn name(&self) -> &'static str {
        "bass_drive"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.freq.set(value.log2()),
            _ => {
                self.drive.set(amplitude(value));
                self.wet.set(if value > 0.0 { 1.0 } else { 0.0 });
            }
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = usable_rate(rate);
        for s in [&mut self.freq, &mut self.drive, &mut self.wet] {
            s.reset(self.rate);
        }
        self.redesign();
        let sub_hz = SUB_HZ.min(0.45 * self.rate as f64);
        let sub = design::butterworth(BUTTERWORTH_ORDER, Pass::Lowpass(sub_hz), self.rate as f64);
        for ch in 0..2 {
            for s in self.low[ch].iter_mut().chain(&mut self.high[ch]) {
                s.clear();
            }
            for chain in [&mut self.sub_before[ch], &mut self.sub_after[ch]] {
                for (s, c) in chain.iter_mut().zip(&sub) {
                    s.set(*c);
                    s.clear();
                }
            }
        }
        self.power_before = 0.0;
        self.power_after = 0.0;
        self.keep = (-1.0 / (SUB_SECONDS as f64 * self.rate as f64)).exp();
        self.level = 1.0;
        self.over.clear();
        self.past = [[[0.0; 2]; 2]; OVERSAMPLING_LATENCY];
        self.pos = 0;
        self.countdown = 0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            if self.countdown == 0 {
                self.countdown = REDESIGN;
                if !self.freq.settled() {
                    self.freq.skip(REDESIGN);
                    self.redesign();
                }
            }
            self.countdown -= 1;
            let (drive, wet) = (self.drive.next(), self.wet.next());
            let mut low = [0.0f32; 2];
            let mut high = [0.0f32; 2];
            let mut driven = [0.0f32; 2];
            let (mut before, mut after) = (0.0f64, 0.0f64);
            for ch in 0..2 {
                let [l1, l2] = &mut self.low[ch];
                let [h1, h2] = &mut self.high[ch];
                low[ch] = l2.tick(l1.tick(f[ch]));
                high[ch] = h2.tick(h1.tick(f[ch]));
                driven[ch] = self.over.tick(ch, low[ch], |u| (u * drive).tanh());
                let b = self.sub_before[ch]
                    .iter_mut()
                    .fold(low[ch], |v, s| s.tick(v));
                let a = self.sub_after[ch]
                    .iter_mut()
                    .fold(driven[ch], |v, s| s.tick(v));
                before += (b as f64).powi(2);
                after += (a as f64).powi(2);
            }
            self.power_before = before + (self.power_before - before) * self.keep;
            self.power_after = after + (self.power_after - after) * self.keep;
            if self.power_before > 1e-20 && self.power_after > 1e-20 {
                self.level = (self.power_before / self.power_after).sqrt() as f32;
            }
            // the input and the high band from as long ago as the driven band's frame
            let [dry, late_high] = self.past[self.pos];
            self.past[self.pos] = [*f, high];
            self.pos = (self.pos + 1) % OVERSAMPLING_LATENCY;
            for ch in 0..2 {
                let wet_out = late_high[ch] + self.level * driven[ch];
                f[ch] = if wet == 0.0 {
                    dry[ch]
                } else {
                    dry[ch] + (wet_out - dry[ch]) * wet
                };
            }
        }
    }
    fn latency(&self) -> usize {
        OVERSAMPLING_LATENCY
    }
}

pub struct Master {
    gain: Smoothed,
    trim: Smoothed,
    ceiling: Smoothed,
    rate: f32,
    band: [[[Sec; SECTIONS]; CLIP_PASSES]; 2],
    late: usize,
    over: Oversampler,
}

impl Master {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("gain", -30.0, 30.0, 0.0, "dB"),
        ParamSpec::range("trim", -12.0, 0.0, 0.0, "dB"),
        ParamSpec::range("ceiling", -6.0, 0.0, -0.1, "dB"),
    ];

    pub fn new() -> Self {
        let mut m = Master {
            gain: Smoothed::new(1.0),
            trim: Smoothed::new(1.0),
            ceiling: Smoothed::new(amplitude(-0.1)),
            rate: DEFAULT_RATE,
            band: [[[Sec::new(); SECTIONS]; CLIP_PASSES]; 2],
            late: 0,
            over: Oversampler::new(MASTER_FACTOR),
        };
        m.reset(DEFAULT_RATE);
        m
    }
}

/// The delay of a cascade of sections at 0 Hz, in samples.
fn delay_at_dc(sections: &[design::Sos]) -> f64 {
    sections
        .iter()
        .map(|c| {
            let (b, a) = (c[0] + c[1] + c[2], 1.0 + c[4] + c[5]);
            (c[1] + 2.0 * c[2]) / b - (c[4] + 2.0 * c[5]) / a
        })
        .sum()
}

impl Effect for Master {
    fn name(&self) -> &'static str {
        "master"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.gain.set(amplitude(value)),
            1 => self.trim.set(amplitude(value)),
            _ => self.ceiling.set(amplitude(value)),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = usable_rate(rate);
        for s in [&mut self.gain, &mut self.trim, &mut self.ceiling] {
            s.reset(self.rate);
        }
        let fast = self.rate as f64 * MASTER_FACTOR as f64;
        let band = design::butterworth(
            BUTTERWORTH_ORDER,
            Pass::Lowpass(BAND_LIMIT_HZ.min(0.45 * fast)),
            fast,
        );
        for passes in &mut self.band {
            for chain in passes {
                for (s, c) in chain.iter_mut().zip(&band) {
                    s.set(*c);
                    s.clear();
                }
            }
        }
        // the band limit is late too, by about its delay at low frequencies on each pass
        let fast_frames = CLIP_PASSES as f64 * delay_at_dc(&band);
        self.late = OVERSAMPLING_LATENCY + (fast_frames / MASTER_FACTOR as f64).round() as usize;
        self.over.clear();
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let (gain, trim, c) = (self.gain.next(), self.trim.next(), self.ceiling.next());
            for (ch, v) in f.iter_mut().enumerate() {
                let band = &mut self.band[ch];
                let y = self.over.tick(ch, *v * gain, |u| {
                    let mut s = u;
                    for chain in band.iter_mut() {
                        s = chain.iter_mut().fold(s.clamp(-c, c), |x, sec| sec.tick(x));
                    }
                    s.clamp(-c, c)
                });
                *v = (y * trim).clamp(-c, c);
            }
        }
    }
    fn latency(&self) -> usize {
        self.late
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bursts(frames: usize) -> Vec<[f32; 2]> {
        (0..frames)
            .map(|n| {
                let v = if (n / 700) % 2 == 0 { 0.9 } else { 0.01 };
                let s = v * (n as f32 * 0.05).sin();
                [s, s]
            })
            .collect()
    }

    // 2^32 frames is under a day at 48 kHz; on wasm32 a usize counter wrapped there
    #[test]
    fn the_compressor_goes_on_past_two_to_the_32_frames() {
        let mut from_zero = ChainCompressor::new();
        from_zero.reset(48000.0);
        let mut near_wrap = ChainCompressor::new();
        near_wrap.reset(48000.0);
        // a whole number of the 8-block ring, so both read the same smoothed slots
        let ring = 8 * near_wrap.block as u64;
        let start = ((1u64 << 32) - 3000) / ring * ring;
        near_wrap.now = start.try_into().expect("the counter holds 2^32");
        let input = bursts(20_000);
        let mut a = input.clone();
        let mut b = input;
        for (x, y) in a.chunks_mut(128).zip(b.chunks_mut(128)) {
            from_zero.process(x);
            near_wrap.process(y);
        }
        // the first half block out reads the slot before the start, which only the counter at
        // 0 clamps to its first one
        let skip = (LOOKAHEAD_BLOCKS + 1) * near_wrap.block;
        let worst = a[skip..]
            .iter()
            .zip(&b[skip..])
            .map(|(x, y)| (x[0] - y[0]).abs().max((x[1] - y[1]).abs()))
            .fold(0.0f32, f32::max);
        assert!(worst < 1e-6, "differs by {worst} past the wrap");
        assert!(
            a.iter().any(|f| f[0].abs() > 1e-3),
            "the input came through"
        );
    }
}
