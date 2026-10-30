//! The dynamics of the voice lab, and the small parts the colour effects share with them.
//!
//! - compressor: the level is the RMS of both channels over a 5 ms window, from the input or
//!   from the side signal. Reduction follows the static curve of Giannoulis, Massberg and
//!   Reiss (threshold, ratio, a quadratic knee), smoothed in dB by attack and release.
//! - gate: open while the peak of either channel is over the threshold and for `hold` after
//!   the last such sample; then the gain falls in a straight line to `range` dB down, from
//!   fully open to closed over `release`, and rises again over `attack`.
//! - deesser: a band around `frequency` (a band pass of q 1) is taken out of the signal and
//!   put back turned down, so nothing else moves. The band's level is measured through a
//!   second band pass and held to the threshold, at most `range` dB down.
//! - limiter: the gain each sample needs to stay under the ceiling, its minimum over the
//!   lookahead, a release, then a moving average over the lookahead. Every gain averaged
//!   into a sample is at most the one that sample needs, so the delayed output stays under
//!   the ceiling; the latency is the lookahead.
//! - multiband: Linkwitz-Riley crossovers of the fourth order split three bands that sum
//!   back to an all-pass; each band has its own threshold and ratio.
//! - duck: the main signal goes `depth` dB down while the side's RMS level is over the
//!   threshold (the full depth 3 dB over it, none 3 dB under).
//! - transient: a fast and a slow envelope tell an attack from what follows; the attack is
//!   raised or lowered by `attack` dB, and the decay by `sustain` dB.

use crate::design::{self, Sos};
use crate::effect::{check, finite, Effect, ParamError, ParamSpec, Smoothed, MAX_BLOCK};

pub(crate) const DEFAULT_RATE: f32 = 48000.0;

pub(crate) fn amplitude(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

/// dB of a power; silence is far down rather than minus infinity.
pub(crate) fn power_db(power: f32) -> f32 {
    10.0 * power.max(1e-30).log10()
}

/// The pole of a one-pole smoother with time constant `ms`: each sample keeps this much of
/// the old value. 0, no smoothing at all, when the time is under a sample.
pub(crate) fn pole(ms: f32, rate: f32) -> f32 {
    let samples = ms * 0.001 * rate;
    if samples < 1e-3 {
        0.0
    } else {
        (-1.0 / samples).exp()
    }
}

pub(crate) fn frames(ms: f32, rate: f32) -> usize {
    (ms * 0.001 * rate).round() as usize
}

/// A corner frequency kept under the Nyquist frequency of the rate.
pub(crate) fn under_nyquist(freq: f32, rate: f32) -> f64 {
    freq.min(0.45 * rate) as f64
}

/// The values of every param at its default.
pub(crate) fn defaults<const N: usize>(specs: &[ParamSpec]) -> [f32; N] {
    let mut v = [0.0; N];
    for (slot, s) in v.iter_mut().zip(specs) {
        *slot = s.default;
    }
    v
}

/// One mono second-order section, direct form II transposed, in double precision.
#[derive(Clone, Copy)]
pub(crate) struct Sec {
    c: Sos,
    z: [f64; 2],
}

impl Sec {
    pub(crate) const PASS: Sos = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];

    pub(crate) fn new() -> Self {
        Sec {
            c: Self::PASS,
            z: [0.0; 2],
        }
    }

    pub(crate) fn set(&mut self, c: Sos) {
        self.c = c;
    }

    pub(crate) fn clear(&mut self) {
        self.z = [0.0; 2];
    }

    #[inline]
    pub(crate) fn tick(&mut self, x: f32) -> f32 {
        let (c, x) = (&self.c, x as f64);
        let y = c[0] * x + self.z[0];
        self.z[0] = c[1] * x - c[4] * y + self.z[1];
        self.z[1] = c[2] * x - c[5] * y;
        // stop a ringing tail at -600 dB instead of crawling through subnormals
        if self.z[0].abs() < 1e-30 && self.z[1].abs() < 1e-30 {
            self.z = [0.0; 2];
        }
        y as f32
    }
}

/// The side signal of the next block, copied in and cleaned of non-finite samples.
pub(crate) struct Side {
    buf: Vec<[f32; 2]>,
    len: usize,
}

impl Side {
    pub(crate) fn new() -> Self {
        Side {
            buf: vec![[0.0; 2]; MAX_BLOCK],
            len: 0,
        }
    }

    pub(crate) fn take(&mut self, side: &[[f32; 2]]) {
        let n = side.len().min(self.buf.len());
        self.buf[..n].copy_from_slice(&side[..n]);
        finite(&mut self.buf[..n]);
        self.len = n;
    }

    /// Frame `k` of the block; silence past the side's end.
    #[inline]
    pub(crate) fn at(&self, k: usize) -> [f32; 2] {
        if k < self.len {
            self.buf[k]
        } else {
            [0.0; 2]
        }
    }

    /// The block is done: the next one has a silent side unless it is given one.
    pub(crate) fn done(&mut self) {
        self.len = 0;
    }
}

const RMS_MS: f32 = 5.0;

/// Mean square of a stereo frame.
#[inline]
fn power(f: [f32; 2]) -> f32 {
    0.5 * (f[0] * f[0] + f[1] * f[1])
}

/// Gain reduction in dB, Giannoulis, Massberg and Reiss (2012): none under the knee, (L - T)
/// times (1 - 1/R) over it, a quadratic between.
fn reduction(level: f32, threshold: f32, ratio: f32, knee: f32) -> f32 {
    let over = level - threshold;
    let slope = 1.0 - 1.0 / ratio;
    if 2.0 * over <= -knee {
        0.0
    } else if knee > 0.0 && 2.0 * over.abs() <= knee {
        slope * (over + 0.5 * knee).powi(2) / (2.0 * knee)
    } else {
        over * slope
    }
}

/// An RMS detector, the static curve and attack/release ballistics on the reduction.
#[derive(Clone, Copy)]
struct Comp {
    threshold: f32,
    ratio: f32,
    knee: f32,
    attack_ms: f32,
    release_ms: f32,
    window: f32,
    attack: f32,
    release: f32,
    power: f32,
    gr: f32,
}

impl Comp {
    fn new(threshold: f32, ratio: f32, knee: f32, attack_ms: f32, release_ms: f32) -> Self {
        let mut c = Comp {
            threshold,
            ratio,
            knee,
            attack_ms,
            release_ms,
            window: 0.0,
            attack: 0.0,
            release: 0.0,
            power: 0.0,
            gr: 0.0,
        };
        c.times(DEFAULT_RATE);
        c
    }

    fn times(&mut self, rate: f32) {
        self.window = pole(RMS_MS, rate);
        self.attack = pole(self.attack_ms, rate);
        self.release = pole(self.release_ms, rate);
    }

    fn reset(&mut self, rate: f32) {
        self.times(rate);
        self.power = 0.0;
        self.gr = 0.0;
    }

    /// Takes the detector's next power, gives the reduction in dB.
    #[inline]
    fn next(&mut self, p: f32) -> f32 {
        self.power = p + (self.power - p) * self.window;
        let target = reduction(power_db(self.power), self.threshold, self.ratio, self.knee);
        let a = if target > self.gr {
            self.attack
        } else {
            self.release
        };
        self.gr = target + (self.gr - target) * a;
        self.gr
    }
}

pub struct Compressor {
    comp: Comp,
    detector: usize,
    makeup: Smoothed,
    rate: f32,
    side: Side,
}

impl Compressor {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("threshold", -60.0, 0.0, -18.0, "dB"),
        ParamSpec::range("ratio", 1.0, 20.0, 4.0, ":1"),
        ParamSpec::range("attack", 0.1, 200.0, 10.0, "ms"),
        ParamSpec::range("release", 5.0, 2000.0, 100.0, "ms"),
        ParamSpec::range("knee", 0.0, 24.0, 6.0, "dB"),
        ParamSpec::range("makeup", -12.0, 24.0, 0.0, "dB"),
        ParamSpec::choice("detector", &["input", "side"]),
    ];

    pub fn new() -> Self {
        let [threshold, ratio, attack, release, knee, makeup, detector] = defaults(Self::SPECS);
        Compressor {
            comp: Comp::new(threshold, ratio, knee, attack, release),
            detector: detector as usize,
            makeup: Smoothed::new(amplitude(makeup)),
            rate: DEFAULT_RATE,
            side: Side::new(),
        }
    }
}

impl Effect for Compressor {
    fn name(&self) -> &'static str {
        "compressor"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        let c = &mut self.comp;
        match i {
            0 => c.threshold = value,
            1 => c.ratio = value,
            2 => c.attack_ms = value,
            3 => c.release_ms = value,
            4 => c.knee = value,
            5 => self.makeup.set(amplitude(value)),
            _ => self.detector = value.round() as usize,
        }
        c.times(self.rate);
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.comp.reset(rate);
        self.makeup.reset(rate);
        self.side.done();
    }
    fn sidechain(&mut self, side: &[[f32; 2]]) {
        self.side.take(side);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for (k, f) in block.iter_mut().enumerate() {
            let key = if self.detector == 1 {
                self.side.at(k)
            } else {
                *f
            };
            let g = amplitude(-self.comp.next(power(key))) * self.makeup.next();
            f[0] *= g;
            f[1] *= g;
        }
        self.side.done();
    }
}

pub struct Gate {
    values: [f32; 5],
    rate: f32,
    open_for: usize,
    gain: f64,
    floor: f64,
    up: f64,
    down: f64,
    hold: usize,
    level: f32,
}

impl Gate {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("threshold", -80.0, 0.0, -40.0, "dB"),
        ParamSpec::range("attack", 0.1, 50.0, 1.0, "ms"),
        ParamSpec::range("hold", 0.0, 500.0, 50.0, "ms"),
        ParamSpec::range("release", 1.0, 2000.0, 100.0, "ms"),
        ParamSpec::range("range", 0.0, 100.0, 80.0, "dB"),
    ];

    pub fn new() -> Self {
        let mut g = Gate {
            values: defaults(Self::SPECS),
            rate: DEFAULT_RATE,
            open_for: 0,
            gain: 1.0,
            floor: 0.0,
            up: 0.0,
            down: 0.0,
            hold: 0,
            level: 0.0,
        };
        g.apply();
        g.gain = g.floor;
        g
    }

    fn apply(&mut self) {
        let [threshold, attack, hold, release, range] = self.values;
        self.level = amplitude(threshold);
        // a whole swing, closed to open or back, takes the attack or the release
        self.up = 1.0 / (attack as f64 * 0.001 * self.rate as f64).max(1.0);
        self.down = 1.0 / (release as f64 * 0.001 * self.rate as f64).max(1.0);
        self.hold = frames(hold, self.rate);
        self.floor = amplitude(-range) as f64;
    }
}

impl Effect for Gate {
    fn name(&self) -> &'static str {
        "gate"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        self.values[check(self.name(), Self::SPECS, name, value)?] = value;
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.apply();
        self.open_for = 0;
        self.gain = self.floor;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let open = if f[0].abs().max(f[1].abs()) > self.level {
                self.open_for = self.hold;
                true
            } else if self.open_for > 0 {
                self.open_for -= 1;
                true
            } else {
                false
            };
            let target = if open { 1.0 } else { self.floor };
            self.gain = if self.gain < target {
                (self.gain + self.up).min(target)
            } else {
                (self.gain - self.down).max(target)
            };
            let g = self.gain as f32;
            f[0] *= g;
            f[1] *= g;
        }
    }
}

const DEESS_ATTACK_MS: f32 = 0.5;
const DEESS_RELEASE_MS: f32 = 60.0;
const DEESS_WINDOW_MS: f32 = 2.0;

pub struct Deesser {
    values: [f32; 3],
    rate: f32,
    band: [Sec; 2],
    listen: [Sec; 2],
    power: f32,
    gr: f32,
    window: f32,
    attack: f32,
    release: f32,
}

impl Deesser {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("frequency", 2000.0, 12000.0, 6500.0, "Hz"),
        ParamSpec::range("threshold", -60.0, 0.0, -30.0, "dB"),
        ParamSpec::range("range", 0.0, 24.0, 12.0, "dB"),
    ];

    pub fn new() -> Self {
        let mut d = Deesser {
            values: defaults(Self::SPECS),
            rate: DEFAULT_RATE,
            band: [Sec::new(); 2],
            listen: [Sec::new(); 2],
            power: 0.0,
            gr: 0.0,
            window: 0.0,
            attack: 0.0,
            release: 0.0,
        };
        d.apply();
        d
    }

    fn apply(&mut self) {
        let f = under_nyquist(self.values[0], self.rate);
        let c = design::bandpass(f, 1.0, self.rate as f64);
        for s in self.band.iter_mut().chain(&mut self.listen) {
            s.set(c);
        }
        self.window = pole(DEESS_WINDOW_MS, self.rate);
        self.attack = pole(DEESS_ATTACK_MS, self.rate);
        self.release = pole(DEESS_RELEASE_MS, self.rate);
    }
}

impl Effect for Deesser {
    fn name(&self) -> &'static str {
        "deesser"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        self.values[check(self.name(), Self::SPECS, name, value)?] = value;
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.apply();
        for s in self.band.iter_mut().chain(&mut self.listen) {
            s.clear();
        }
        self.power = 0.0;
        self.gr = 0.0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        let [_, threshold, range] = self.values;
        for f in block {
            let band = [self.band[0].tick(f[0]), self.band[1].tick(f[1])];
            let heard = [self.listen[0].tick(band[0]), self.listen[1].tick(band[1])];
            let p = power(heard);
            self.power = p + (self.power - p) * self.window;
            let target = (power_db(self.power) - threshold).clamp(0.0, range);
            let a = if target > self.gr {
                self.attack
            } else {
                self.release
            };
            self.gr = target + (self.gr - target) * a;
            let cut = 1.0 - amplitude(-self.gr);
            f[0] -= cut * band[0];
            f[1] -= cut * band[1];
        }
    }
}

const LOOKAHEAD_MAX_MS: f32 = 10.0;

pub struct Limiter {
    values: [f32; 3],
    rate: f32,
    lag: usize,
    ceiling: f32,
    release: f32,
    delay: Vec<[f32; 2]>,
    pos: usize,
    // a monotonic queue of (sample number, needed gain) for the minimum over the window
    queue: Vec<(u64, f32)>,
    head: usize,
    count: usize,
    held: f32,
    // the last window of released gains and their sum, for the moving average
    recent: Vec<f32>,
    sum: f64,
    n: u64,
}

impl Limiter {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("ceiling", -24.0, 0.0, -1.0, "dB"),
        ParamSpec::range("release", 1.0, 1000.0, 50.0, "ms"),
        ParamSpec::range("lookahead", 0.1, LOOKAHEAD_MAX_MS, 5.0, "ms"),
    ];

    pub fn new() -> Self {
        let mut l = Limiter {
            values: defaults(Self::SPECS),
            rate: DEFAULT_RATE,
            lag: 0,
            ceiling: 1.0,
            release: 0.0,
            delay: Vec::new(),
            pos: 0,
            queue: Vec::new(),
            head: 0,
            count: 0,
            held: 1.0,
            recent: Vec::new(),
            sum: 0.0,
            n: 0,
        };
        l.apply();
        l
    }

    fn apply(&mut self) {
        self.ceiling = amplitude(self.values[0]);
        self.release = pole(self.values[1], self.rate);
    }

    #[inline]
    fn window(&self) -> usize {
        self.lag + 1
    }
}

impl Effect for Limiter {
    fn name(&self) -> &'static str {
        "limiter"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    /// The lookahead sets the latency, so a new one applies from the next reset.
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        self.values[check(self.name(), Self::SPECS, name, value)?] = value;
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.apply();
        self.lag = frames(self.values[2], rate);
        let most = frames(LOOKAHEAD_MAX_MS, rate) + 1;
        self.delay.clear();
        self.delay.resize(most, [0.0; 2]);
        self.queue.clear();
        self.queue.resize(most, (0, 1.0));
        self.recent.clear();
        self.recent.resize(most, 1.0);
        self.pos = 0;
        self.head = 0;
        self.count = 0;
        self.held = 1.0;
        self.sum = self.window() as f64;
        self.n = 0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.delay.is_empty() {
            return;
        }
        finite(block);
        let (w, cap) = (self.window(), self.queue.len());
        for f in block {
            let loudest = f[0].abs().max(f[1].abs());
            let need = if loudest > self.ceiling {
                self.ceiling / loudest
            } else {
                1.0
            };
            // the window's minimum: drop larger gains from the back, expired ones from the front
            while self.count > 0 && self.queue[(self.head + self.count - 1) % cap].1 >= need {
                self.count -= 1;
            }
            self.queue[(self.head + self.count) % cap] = (self.n, need);
            self.count += 1;
            while self.queue[self.head].0 + (w as u64) <= self.n {
                self.head = (self.head + 1) % cap;
                self.count -= 1;
            }
            let least = self.queue[self.head].1;
            self.held = if least < self.held {
                least
            } else {
                least + (self.held - least) * self.release
            };
            let slot = (self.n % w as u64) as usize;
            self.sum += self.held as f64 - self.recent[slot] as f64;
            self.recent[slot] = self.held;
            if slot == w - 1 {
                // a fresh sum each window, so rounding never builds up
                self.sum = self.recent[..w].iter().map(|&g| g as f64).sum();
            }
            let gain = (self.sum / w as f64) as f32;
            let out = if self.lag == 0 {
                *f
            } else {
                let d = std::mem::replace(&mut self.delay[self.pos], *f);
                self.pos = (self.pos + 1) % self.lag;
                d
            };
            let c = self.ceiling;
            // the average is under each gain it takes in; this only catches rounding
            *f = [(out[0] * gain).clamp(-c, c), (out[1] * gain).clamp(-c, c)];
            self.n += 1;
        }
    }
    fn latency(&self) -> usize {
        self.lag
    }
}

/// A Linkwitz-Riley crossover of the fourth order: two Butterworth sections each way.
#[derive(Clone, Copy)]
struct Split {
    low: [Sec; 2],
    high: [Sec; 2],
}

impl Split {
    fn new() -> Self {
        Split {
            low: [Sec::new(); 2],
            high: [Sec::new(); 2],
        }
    }

    fn design(&mut self, freq: f32, rate: f32) {
        let f = under_nyquist(freq, rate);
        let q = std::f64::consts::FRAC_1_SQRT_2;
        let (lp, hp) = (
            design::lowpass(f, q, rate as f64),
            design::highpass(f, q, rate as f64),
        );
        for s in &mut self.low {
            s.set(lp);
        }
        for s in &mut self.high {
            s.set(hp);
        }
    }

    fn clear(&mut self) {
        for s in self.low.iter_mut().chain(&mut self.high) {
            s.clear();
        }
    }

    #[inline]
    fn tick(&mut self, x: f32) -> (f32, f32) {
        let lo = self.low[0].tick(x);
        let lo = self.low[1].tick(lo);
        let hi = self.high[0].tick(x);
        let hi = self.high[1].tick(hi);
        (lo, hi)
    }
}

pub struct Multiband {
    values: [f32; 11],
    rate: f32,
    // per channel: the low crossover, the high one, and the high one's all-pass on the low band
    splits: [[Split; 3]; 2],
    bands: [Comp; 3],
    makeup: Smoothed,
}

impl Multiband {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("low", 40.0, 1000.0, 200.0, "Hz"),
        ParamSpec::range("high", 1000.0, 12000.0, 3000.0, "Hz"),
        ParamSpec::range("low_threshold", -60.0, 0.0, 0.0, "dB"),
        ParamSpec::range("low_ratio", 1.0, 20.0, 2.0, ":1"),
        ParamSpec::range("mid_threshold", -60.0, 0.0, 0.0, "dB"),
        ParamSpec::range("mid_ratio", 1.0, 20.0, 2.0, ":1"),
        ParamSpec::range("high_threshold", -60.0, 0.0, 0.0, "dB"),
        ParamSpec::range("high_ratio", 1.0, 20.0, 2.0, ":1"),
        ParamSpec::range("attack", 0.1, 200.0, 10.0, "ms"),
        ParamSpec::range("release", 5.0, 2000.0, 150.0, "ms"),
        ParamSpec::range("makeup", -12.0, 24.0, 0.0, "dB"),
    ];

    pub fn new() -> Self {
        let mut m = Multiband {
            values: defaults(Self::SPECS),
            rate: DEFAULT_RATE,
            splits: [[Split::new(); 3]; 2],
            bands: [Comp::new(0.0, 1.0, 0.0, 10.0, 100.0); 3],
            makeup: Smoothed::new(1.0),
        };
        m.apply();
        m
    }

    fn apply(&mut self) {
        let v = self.values;
        for ch in &mut self.splits {
            ch[0].design(v[0], self.rate);
            ch[1].design(v[1], self.rate);
            ch[2].design(v[1], self.rate);
        }
        for (b, c) in self.bands.iter_mut().enumerate() {
            c.threshold = v[2 + 2 * b];
            c.ratio = v[3 + 2 * b];
            c.knee = 0.0;
            c.attack_ms = v[8];
            c.release_ms = v[9];
            c.times(self.rate);
        }
        self.makeup.set(amplitude(v[10]));
    }
}

impl Effect for Multiband {
    fn name(&self) -> &'static str {
        "multiband"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        self.values[check(self.name(), Self::SPECS, name, value)?] = value;
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.apply();
        for s in self.splits.as_flattened_mut() {
            s.clear();
        }
        for c in &mut self.bands {
            c.reset(rate);
        }
        self.makeup.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let mut bands = [[0.0f32; 2]; 3];
            for (c, s) in self.splits.iter_mut().enumerate() {
                let (low, rest) = s[0].tick(f[c]);
                let (mid, high) = s[1].tick(rest);
                let (a, b) = s[2].tick(low);
                bands[0][c] = a + b;
                bands[1][c] = mid;
                bands[2][c] = high;
            }
            let makeup = self.makeup.next();
            let mut out = [0.0f32; 2];
            for (band, comp) in bands.iter().zip(&mut self.bands) {
                let g = amplitude(-comp.next(power(*band)));
                out[0] += g * band[0];
                out[1] += g * band[1];
            }
            *f = [out[0] * makeup, out[1] * makeup];
        }
    }
}

/// The span around the threshold over which the duck goes from none to its full depth.
const DUCK_KNEE: f32 = 6.0;

pub struct Duck {
    values: [f32; 4],
    rate: f32,
    window: f32,
    attack: f32,
    release: f32,
    power: f32,
    gr: f32,
    side: Side,
}

impl Duck {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("threshold", -60.0, 0.0, -30.0, "dB"),
        ParamSpec::range("depth", 0.0, 48.0, 12.0, "dB"),
        ParamSpec::range("attack", 0.1, 500.0, 10.0, "ms"),
        ParamSpec::range("release", 5.0, 2000.0, 200.0, "ms"),
    ];

    pub fn new() -> Self {
        let mut d = Duck {
            values: defaults(Self::SPECS),
            rate: DEFAULT_RATE,
            window: 0.0,
            attack: 0.0,
            release: 0.0,
            power: 0.0,
            gr: 0.0,
            side: Side::new(),
        };
        d.apply();
        d
    }

    fn apply(&mut self) {
        self.window = pole(RMS_MS, self.rate);
        self.attack = pole(self.values[2], self.rate);
        self.release = pole(self.values[3], self.rate);
    }
}

impl Effect for Duck {
    fn name(&self) -> &'static str {
        "duck"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        self.values[check(self.name(), Self::SPECS, name, value)?] = value;
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.apply();
        self.power = 0.0;
        self.gr = 0.0;
        self.side.done();
    }
    fn sidechain(&mut self, side: &[[f32; 2]]) {
        self.side.take(side);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        let [threshold, depth, _, _] = self.values;
        for (k, f) in block.iter_mut().enumerate() {
            let p = power(self.side.at(k));
            self.power = p + (self.power - p) * self.window;
            let over = (power_db(self.power) - threshold) / DUCK_KNEE + 0.5;
            let target = depth * over.clamp(0.0, 1.0);
            let a = if target > self.gr {
                self.attack
            } else {
                self.release
            };
            self.gr = target + (self.gr - target) * a;
            let g = amplitude(-self.gr);
            f[0] *= g;
            f[1] *= g;
        }
        self.side.done();
    }
}

/// An envelope follower on a level, with its own attack and release.
#[derive(Clone, Copy)]
struct Follower {
    attack: f32,
    release: f32,
    value: f32,
}

impl Follower {
    fn new() -> Self {
        Follower {
            attack: 0.0,
            release: 0.0,
            value: 0.0,
        }
    }

    fn times(&mut self, attack_ms: f32, release_ms: f32, rate: f32) {
        self.attack = pole(attack_ms, rate);
        self.release = pole(release_ms, rate);
    }

    #[inline]
    fn next(&mut self, x: f32) -> f32 {
        let a = if x > self.value {
            self.attack
        } else {
            self.release
        };
        self.value = x + (self.value - x) * a;
        self.value
    }
}

/// How far apart, in dB, the two envelopes are when the attack or the decay counts fully.
const TRANSIENT_SPAN: f32 = 6.0;

pub struct Transient {
    attack: Smoothed,
    sustain: Smoothed,
    rate: f32,
    // fast and slow on the rise; fast and slow on the fall
    fast: Follower,
    slow: Follower,
    short: Follower,
    long: Follower,
}

impl Transient {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("attack", -24.0, 24.0, 0.0, "dB"),
        ParamSpec::range("sustain", -24.0, 24.0, 0.0, "dB"),
    ];

    pub fn new() -> Self {
        let mut t = Transient {
            attack: Smoothed::new(0.0),
            sustain: Smoothed::new(0.0),
            rate: DEFAULT_RATE,
            fast: Follower::new(),
            slow: Follower::new(),
            short: Follower::new(),
            long: Follower::new(),
        };
        t.times();
        t
    }

    fn times(&mut self) {
        let r = self.rate;
        self.fast.times(0.5, 50.0, r);
        self.slow.times(25.0, 50.0, r);
        self.short.times(0.5, 50.0, r);
        self.long.times(0.5, 400.0, r);
    }
}

impl Effect for Transient {
    fn name(&self) -> &'static str {
        "transient"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.attack.set(value),
            _ => self.sustain.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.times();
        self.attack.reset(rate);
        self.sustain.reset(rate);
        for f in [
            &mut self.fast,
            &mut self.slow,
            &mut self.short,
            &mut self.long,
        ] {
            f.value = 0.0;
        }
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let level = f[0].abs().max(f[1].abs());
            let db = |v: f32| 20.0 * v.max(1e-9).log10();
            let rise = db(self.fast.next(level)) - db(self.slow.next(level));
            let fall = db(self.long.next(level)) - db(self.short.next(level));
            let (attack, sustain) = (self.attack.next(), self.sustain.next());
            if attack == 0.0 && sustain == 0.0 {
                continue;
            }
            let gain_db = attack * (rise / TRANSIENT_SPAN).clamp(0.0, 1.0)
                + sustain * (fall / TRANSIENT_SPAN).clamp(0.0, 1.0);
            let g = amplitude(gain_db);
            f[0] *= g;
            f[1] *= g;
        }
    }
}
