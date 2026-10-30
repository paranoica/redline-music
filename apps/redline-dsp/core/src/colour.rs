//! The colour effects of the voice lab.
//!
//! - distort: hard clip, a triangle fold, or an asymmetric tube curve (with its DC taken
//!   out), then a low pass `tone`, mixed with the dry sound. Not oversampled: it aliases.
//! - downsample: each sample held until the next tick of a clock at `rate`, with no filter
//!   on purpose, so the images stay.
//! - telephone: a fourth-order Butterworth band 300-3400 Hz and a soft clip, tanh(dx)/d.
//! - radio: seeded white noise at `noise` dB added, a band 500-4000 Hz, then the soft clip.
//! - lofi: wow (0.5 Hz, up to 40 cents) and flutter (7 Hz, up to 8 cents) through a swinging
//!   delay whose centre is the latency, a sample-and-hold at `rate`, `bits` of resolution and
//!   seeded noise. The seed sets the wow's and flutter's phases and the noise.
//! - underwater: a steep low pass whose cutoff swings by up to an octave at `speed`, after a
//!   slowly swinging delayed copy (the chorus) is mixed in.
//! - whisper, vocoder: a bank of band passes splits the input; each band's envelope is
//!   imposed on the same band of a carrier, whose own envelope is divided out, so the carrier
//!   gives the pitch and the input gives the spectrum and the level. Whisper's carrier is
//!   seeded white noise, the vocoder's a band-limited saw or pulse chord or the side signal.
//! - talkbox: linear prediction of the input every 15 ms over 30 ms gives its formant
//!   envelope, an all-pole lattice whose reflection coefficients glide from one analysis to
//!   the next; the carrier, pre-emphasised and brought to unit RMS, is driven through it.
//! - ring_mod: the input times a sine. freq_shift: single sideband modulation, the input's
//!   analytic signal (Niemitalo's all-pass Hilbert pair) times a complex sine.
//! - comb: a feedback comb, scaled so its peaks sit at 0 dB.
//! - resonator: one feedback comb per chord note, ringing for `decay` seconds (to -60 dB).
//! - exciter: what a curve adds to the input over `frequency` (the curve's straight part
//!   taken out), high passed again and mixed in by `amount`.
//!
//! Chords: the scale degree `degree` of the key in `octave` (octave 4 holds middle C), and
//! `notes` notes stacked in thirds of the scale above it.

use crate::design::{self, Band};
use crate::dynamics::{amplitude, defaults, frames, pole, under_nyquist, Sec, Side, DEFAULT_RATE};
use crate::effect::{check, finite, Effect, ParamError, ParamSpec, Smoothed};
use crate::pitch::{freq, Scale, KEYS, SCALES};
use crate::voice::Random;
use std::f32::consts::PI;
use std::f64::consts::TAU;

fn mono(f: [f32; 2]) -> f32 {
    0.5 * (f[0] + f[1])
}

/// Dry and wet by `mix`: exactly the dry at 0.
#[inline]
fn blend(dry: [f32; 2], wet: [f32; 2], mix: f32) -> [f32; 2] {
    [
        dry[0] + mix * (wet[0] - dry[0]),
        dry[1] + mix * (wet[1] - dry[1]),
    ]
}

/// A one-pole high pass at a few hertz: takes out DC.
#[derive(Clone, Copy)]
struct DcBlock {
    x: f32,
    y: f32,
    r: f32,
}

impl DcBlock {
    fn new() -> Self {
        DcBlock {
            x: 0.0,
            y: 0.0,
            r: 0.999,
        }
    }

    fn reset(&mut self, rate: f32) {
        self.r = (-2.0 * PI * 10.0 / rate).exp();
        self.x = 0.0;
        self.y = 0.0;
    }

    #[inline]
    fn tick(&mut self, x: f32) -> f32 {
        self.y = x - self.x + self.r * self.y;
        self.x = x;
        if self.y.abs() < 1e-30 {
            self.y = 0.0;
        }
        self.y
    }
}

/// A cascade of `N` sections on each channel.
#[derive(Clone, Copy)]
struct Cascade<const N: usize> {
    s: [[Sec; N]; 2],
}

impl<const N: usize> Cascade<N> {
    fn new() -> Self {
        Cascade {
            s: [[Sec::new(); N]; 2],
        }
    }

    fn set(&mut self, i: usize, c: design::Sos) {
        self.s[0][i].set(c);
        self.s[1][i].set(c);
    }

    fn clear(&mut self) {
        for s in self.s.as_flattened_mut() {
            s.clear();
        }
    }

    #[inline]
    fn tick(&mut self, f: [f32; 2]) -> [f32; 2] {
        let mut out = f;
        for (v, chain) in out.iter_mut().zip(&mut self.s) {
            for s in chain.iter_mut() {
                *v = s.tick(*v);
            }
        }
        out
    }
}

/// Fourth-order Butterworth high pass then low pass: a band, as scipy designs it.
fn band(low: f32, high: f32, rate: f32) -> Cascade<4> {
    let mut c = Cascade::new();
    let high = under_nyquist(high, rate);
    let low = under_nyquist(low, rate).min(0.5 * high);
    let sections = design::butterworth(4, Band::Highpass(low), rate as f64)
        .into_iter()
        .chain(design::butterworth(4, Band::Lowpass(high), rate as f64));
    for (i, s) in sections.enumerate() {
        c.set(i, s);
    }
    c
}

/// tanh(d x)/d: unity for quiet sound, rounded off for loud.
#[inline]
fn soft(x: f32, drive: f32) -> f32 {
    (drive * x).tanh() / drive
}

macro_rules! basics {
    ($name:literal) => {
        fn name(&self) -> &'static str {
            $name
        }
        fn params(&self) -> &'static [ParamSpec] {
            Self::SPECS
        }
    };
}

const TUBE_BIAS: f32 = 0.3;

pub struct Distort {
    mode: usize,
    tone: f32,
    rate: f32,
    drive: Smoothed,
    mix: Smoothed,
    filter: Cascade<1>,
    dc: [DcBlock; 2],
}

impl Distort {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::choice("mode", &["hard", "fold", "tube"]),
        ParamSpec::range("drive", 0.0, 48.0, 12.0, "dB"),
        ParamSpec::range("tone", 500.0, 20000.0, 12000.0, "Hz"),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        let [mode, drive, tone, mix] = defaults(Self::SPECS);
        let mut d = Distort {
            mode: mode as usize,
            tone,
            rate: DEFAULT_RATE,
            drive: Smoothed::new(amplitude(drive)),
            mix: Smoothed::new(mix),
            filter: Cascade::new(),
            dc: [DcBlock::new(); 2],
        };
        d.design();
        d
    }

    fn design(&mut self) {
        let f = under_nyquist(self.tone, self.rate);
        let q = std::f64::consts::FRAC_1_SQRT_2;
        self.filter.set(0, design::lowpass(f, q, self.rate as f64));
    }

    #[inline]
    fn shape(&self, u: f32) -> f32 {
        match self.mode {
            0 => u.clamp(-1.0, 1.0),
            // reflected at ±1 as often as it takes
            1 => {
                let v = (u + 1.0).rem_euclid(4.0);
                if v < 2.0 {
                    v - 1.0
                } else {
                    3.0 - v
                }
            }
            _ => (u + TUBE_BIAS).tanh() - TUBE_BIAS.tanh(),
        }
    }
}

impl Effect for Distort {
    basics!("distort");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.mode = value.round() as usize,
            1 => self.drive.set(amplitude(value)),
            2 => {
                self.tone = value;
                self.design();
            }
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.design();
        self.filter.clear();
        for d in &mut self.dc {
            d.reset(rate);
        }
        self.drive.reset(rate);
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let (drive, mix) = (self.drive.next(), self.mix.next());
            let mut wet = [self.shape(drive * f[0]), self.shape(drive * f[1])];
            if self.mode == 2 {
                wet = [self.dc[0].tick(wet[0]), self.dc[1].tick(wet[1])];
            }
            *f = blend(*f, self.filter.tick(wet), mix);
        }
    }
}

pub struct Downsample {
    target: f32,
    rate: f32,
    phase: f64,
    held: [f32; 2],
}

impl Downsample {
    const SPECS: &'static [ParamSpec] = &[ParamSpec::range("rate", 100.0, 48000.0, 8000.0, "Hz")];

    pub fn new() -> Self {
        Downsample {
            target: 8000.0,
            rate: DEFAULT_RATE,
            phase: 1.0,
            held: [0.0; 2],
        }
    }
}

/// Sample and hold on a clock of `step` ticks per sample: true when the clock ticks.
#[inline]
fn tick_clock(phase: &mut f64, step: f64) -> bool {
    if *phase >= 1.0 {
        *phase -= phase.floor();
        *phase += step;
        true
    } else {
        *phase += step;
        false
    }
}

impl Effect for Downsample {
    basics!("downsample");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        check(self.name(), Self::SPECS, name, value)?;
        self.target = value;
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.phase = 1.0;
        self.held = [0.0; 2];
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        let step = self.target as f64 / self.rate as f64;
        for f in block {
            if tick_clock(&mut self.phase, step) {
                self.held = *f;
            }
            *f = self.held;
        }
    }
}

pub struct Telephone {
    rate: f32,
    filter: Cascade<4>,
    drive: Smoothed,
    mix: Smoothed,
}

impl Telephone {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("drive", 0.0, 24.0, 6.0, "dB"),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        Telephone {
            rate: DEFAULT_RATE,
            filter: band(300.0, 3400.0, DEFAULT_RATE),
            drive: Smoothed::new(amplitude(6.0)),
            mix: Smoothed::new(1.0),
        }
    }
}

impl Effect for Telephone {
    basics!("telephone");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.drive.set(amplitude(value)),
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        if rate != self.rate {
            self.filter = band(300.0, 3400.0, rate);
            self.rate = rate;
        }
        self.filter.clear();
        self.drive.reset(rate);
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let (drive, mix) = (self.drive.next(), self.mix.next());
            let b = self.filter.tick(*f);
            *f = blend(*f, [soft(b[0], drive), soft(b[1], drive)], mix);
        }
    }
}

pub struct Radio {
    seed: u64,
    rate: f32,
    random: Random,
    filter: Cascade<4>,
    noise: Smoothed,
    drive: Smoothed,
    mix: Smoothed,
}

impl Radio {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("noise", -90.0, -20.0, -50.0, "dB"),
        ParamSpec::range("drive", 0.0, 24.0, 6.0, "dB"),
        ParamSpec::range("seed", 0.0, 65535.0, 0.0, ""),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        Radio {
            seed: 0,
            rate: DEFAULT_RATE,
            random: Random(0),
            filter: band(500.0, 4000.0, DEFAULT_RATE),
            noise: Smoothed::new(amplitude(-50.0)),
            drive: Smoothed::new(amplitude(6.0)),
            mix: Smoothed::new(1.0),
        }
    }
}

impl Effect for Radio {
    basics!("radio");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.noise.set(amplitude(value)),
            1 => self.drive.set(amplitude(value)),
            2 => self.seed = value.round() as u64,
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        if rate != self.rate {
            self.filter = band(500.0, 4000.0, rate);
            self.rate = rate;
        }
        self.filter.clear();
        self.random = Random(self.seed);
        for s in [&mut self.noise, &mut self.drive, &mut self.mix] {
            s.reset(rate);
        }
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let (noise, drive, mix) = (self.noise.next(), self.drive.next(), self.mix.next());
            let hiss = [noise * self.random.signed(), noise * self.random.signed()];
            let b = self.filter.tick([f[0] + hiss[0], f[1] + hiss[1]]);
            *f = blend(*f, [soft(b[0], drive), soft(b[1], drive)], mix);
        }
    }
}

/// Recent stereo input; read back any delay, between frames by Catmull-Rom.
struct Line {
    buf: Vec<[f32; 2]>,
    newest: usize,
}

impl Line {
    fn new() -> Self {
        Line {
            buf: Vec::new(),
            newest: 0,
        }
    }

    fn reset(&mut self, longest: usize) {
        self.buf.clear();
        self.buf.resize((longest + 4).next_power_of_two(), [0.0; 2]);
        self.newest = 0;
    }

    fn ready(&self) -> bool {
        !self.buf.is_empty()
    }

    #[inline]
    fn push(&mut self, f: [f32; 2]) {
        self.newest = (self.newest + 1) & (self.buf.len() - 1);
        self.buf[self.newest] = f;
    }

    #[inline]
    fn back(&self, delay: usize) -> [f32; 2] {
        self.buf[self.newest.wrapping_sub(delay) & (self.buf.len() - 1)]
    }

    /// Channel `c` `delay` frames back, the newest frame being 0.
    #[inline]
    fn at(&self, delay: f32, c: usize) -> f32 {
        let d = delay.max(0.0);
        let k = d.floor();
        let t = d - k;
        let k = k as usize;
        if t == 0.0 {
            return self.back(k)[c];
        }
        let (ym, y0, y1, y2) = (
            self.back(k.saturating_sub(1))[c],
            self.back(k)[c],
            self.back(k + 1)[c],
            self.back(k + 2)[c],
        );
        let c1 = 0.5 * (y1 - ym);
        let c2 = ym - 2.5 * y0 + 2.0 * y1 - 0.5 * y2;
        let c3 = 0.5 * (y2 - ym) + 1.5 * (y0 - y1);
        ((c3 * t + c2) * t + c1) * t + y0
    }
}

const WOW_HZ: f32 = 0.5;
const WOW_CENTS: f32 = 40.0;
const FLUTTER_HZ: f32 = 7.0;
const FLUTTER_CENTS: f32 = 8.0;

/// Delay swing in frames for a pitch swing of `cents` at `hz`: a delay A sin(wt) bends the
/// pitch by a factor 1 ± Aw.
fn swing(cents: f32, hz: f32, rate: f32) -> f32 {
    cents * std::f32::consts::LN_2 / 1200.0 / (2.0 * PI * hz / rate)
}

pub struct Lofi {
    values: [f32; 7],
    rate: f32,
    line: Line,
    centre: usize,
    phases: [f32; 2],
    random: Random,
    clock: f64,
    held: [f32; 2],
    wow: Smoothed,
    flutter: Smoothed,
    noise: Smoothed,
    mix: Smoothed,
}

impl Lofi {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("bits", 2.0, 16.0, 10.0, "bits"),
        ParamSpec::range("rate", 1000.0, 48000.0, 16000.0, "Hz"),
        ParamSpec::range("wow", 0.0, 1.0, 0.3, ""),
        ParamSpec::range("flutter", 0.0, 1.0, 0.2, ""),
        ParamSpec::range("noise", -90.0, -30.0, -60.0, "dB"),
        ParamSpec::range("seed", 0.0, 65535.0, 0.0, ""),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        let values: [f32; 7] = defaults(Self::SPECS);
        Lofi {
            values,
            rate: DEFAULT_RATE,
            line: Line::new(),
            centre: 0,
            phases: [0.0; 2],
            random: Random(0),
            clock: 1.0,
            held: [0.0; 2],
            wow: Smoothed::new(values[2]),
            flutter: Smoothed::new(values[3]),
            noise: Smoothed::new(amplitude(values[4])),
            mix: Smoothed::new(values[6]),
        }
    }
}

impl Effect for Lofi {
    basics!("lofi");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        self.values[i] = value;
        match i {
            2 => self.wow.set(value),
            3 => self.flutter.set(value),
            4 => self.noise.set(amplitude(value)),
            6 => self.mix.set(value),
            _ => {}
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        let widest = swing(WOW_CENTS, WOW_HZ, rate) + swing(FLUTTER_CENTS, FLUTTER_HZ, rate);
        self.centre = widest.ceil() as usize + 2;
        self.line.reset(self.centre + widest.ceil() as usize + 4);
        self.random = Random(self.values[5].round() as u64);
        self.phases = [
            PI * (self.random.signed() + 1.0),
            PI * (self.random.signed() + 1.0),
        ];
        self.clock = 1.0;
        self.held = [0.0; 2];
        for s in [
            &mut self.wow,
            &mut self.flutter,
            &mut self.noise,
            &mut self.mix,
        ] {
            s.reset(rate);
        }
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.line.ready() {
            return;
        }
        finite(block);
        let rate = self.rate;
        let (wow_swing, flutter_swing) = (
            swing(WOW_CENTS, WOW_HZ, rate),
            swing(FLUTTER_CENTS, FLUTTER_HZ, rate),
        );
        let steps = [2.0 * PI * WOW_HZ / rate, 2.0 * PI * FLUTTER_HZ / rate];
        let step = self.values[1] as f64 / rate as f64;
        let levels = 2f32.powi(self.values[0].round() as i32 - 1);
        for f in block {
            self.line.push(*f);
            let (wow, flutter) = (self.wow.next(), self.flutter.next());
            let delay = self.centre as f32
                + wow * wow_swing * self.phases[0].sin()
                + flutter * flutter_swing * self.phases[1].sin();
            for (p, s) in self.phases.iter_mut().zip(steps) {
                *p += s;
                if *p >= 2.0 * PI {
                    *p -= 2.0 * PI;
                }
            }
            if tick_clock(&mut self.clock, step) {
                self.held = [self.line.at(delay, 0), self.line.at(delay, 1)];
            }
            let noise = self.noise.next();
            let wet = self
                .held
                .map(|v| (v * levels).round() / levels + noise * self.random.signed());
            let dry = self.line.back(self.centre);
            *f = blend(dry, wet, self.mix.next());
        }
    }
    fn latency(&self) -> usize {
        self.centre
    }
}

/// Frames between redesigns of a moving filter, counted from reset.
const REDESIGN: usize = 32;
const CHORUS_MS: f32 = 12.0;
const CHORUS_SWING_MS: f32 = 4.0;
const CHORUS_HZ: f32 = 0.6;

pub struct Underwater {
    values: [f32; 5],
    rate: f32,
    filter: Cascade<2>,
    line: Line,
    lfo: f32,
    chorus_lfo: f32,
    countdown: usize,
    chorus: Smoothed,
    mix: Smoothed,
}

impl Underwater {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("cutoff", 100.0, 3000.0, 500.0, "Hz"),
        ParamSpec::range("depth", 0.0, 1.0, 0.5, "octaves"),
        ParamSpec::range("speed", 0.05, 2.0, 0.25, "Hz"),
        ParamSpec::range("chorus", 0.0, 1.0, 0.4, ""),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        let values: [f32; 5] = defaults(Self::SPECS);
        let mut u = Underwater {
            values,
            rate: DEFAULT_RATE,
            filter: Cascade::new(),
            line: Line::new(),
            lfo: 0.0,
            chorus_lfo: 0.0,
            countdown: 0,
            chorus: Smoothed::new(values[3]),
            mix: Smoothed::new(values[4]),
        };
        u.design();
        u
    }

    fn design(&mut self) {
        let [cutoff, depth, ..] = self.values;
        let f = under_nyquist(cutoff * 2f32.powf(depth * self.lfo.sin()), self.rate);
        let c = design::lowpass(f, 0.9, self.rate as f64);
        self.filter.set(0, c);
        self.filter.set(1, c);
    }
}

impl Effect for Underwater {
    basics!("underwater");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        self.values[i] = value;
        match i {
            3 => self.chorus.set(value),
            4 => self.mix.set(value),
            _ => {}
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.line
            .reset(frames(CHORUS_MS + CHORUS_SWING_MS, rate) + 4);
        self.lfo = 0.0;
        self.chorus_lfo = 0.0;
        self.countdown = 0;
        self.filter.clear();
        self.design();
        self.chorus.reset(rate);
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.line.ready() {
            return;
        }
        finite(block);
        let rate = self.rate;
        let step = 2.0 * PI * self.values[2] / rate;
        let chorus_step = 2.0 * PI * CHORUS_HZ / rate;
        let (centre, reach) = (CHORUS_MS * 0.001 * rate, CHORUS_SWING_MS * 0.001 * rate);
        for f in block {
            if self.countdown == 0 {
                self.countdown = REDESIGN;
                self.design();
            }
            self.countdown -= 1;
            self.lfo = (self.lfo + step) % (2.0 * PI);
            self.chorus_lfo = (self.chorus_lfo + chorus_step) % (2.0 * PI);
            self.line.push(*f);
            let chorus = self.chorus.next();
            let mut wet = *f;
            for (c, v) in wet.iter_mut().enumerate() {
                let swing = (self.chorus_lfo + c as f32 * 0.5 * PI).sin();
                *v += chorus * self.line.at(centre + reach * swing, c);
                *v /= 1.0 + chorus;
            }
            *f = blend(*f, self.filter.tick(wet), self.mix.next());
        }
    }
}

const MAX_BANDS: usize = 32;

/// Where a bank's bands lie, and the q its carrier bands are held to at most.
struct Layout {
    low: f32,
    high: f32,
    carrier_q: f64,
}

const VOCODER: Layout = Layout {
    low: 100.0,
    high: 8000.0,
    carrier_q: f64::INFINITY,
};

/// A whisper has no voicing to carry: no bands down among a voice's fundamentals, and noise
/// bands wider than the harmonics' spacing, since narrow band noise sounds pitched.
const WHISPER: Layout = Layout {
    low: 300.0,
    high: 10000.0,
    carrier_q: 1.0,
};
/// A carrier band quieter than this (mean absolute value) is not brought up to the input's.
const CARRIER_FLOOR: f32 = 1e-3;

/// Band passes on the input and on a carrier; each carrier band is brought to its input
/// band's envelope.
struct Bank {
    count: usize,
    input: [[Sec; 2]; MAX_BANDS],
    carrier: [[Sec; 2]; MAX_BANDS],
    env: [f32; MAX_BANDS],
    car: [f32; MAX_BANDS],
    attack: f32,
    release: f32,
    car_pole: f32,
}

impl Bank {
    fn new() -> Self {
        Bank {
            count: 0,
            input: [[Sec::new(); 2]; MAX_BANDS],
            carrier: [[Sec::new(); 2]; MAX_BANDS],
            env: [0.0; MAX_BANDS],
            car: [0.0; MAX_BANDS],
            attack: 0.0,
            release: 0.0,
            car_pole: 0.0,
        }
    }

    /// `count` bands spaced evenly in pitch; each pass band is as wide as the step.
    fn design(&mut self, count: usize, layout: &Layout, rate: f32) {
        let count = count.clamp(2, MAX_BANDS);
        let high = (under_nyquist(layout.high, rate) as f32).max(2.0);
        let low = layout.low.min(0.5 * high);
        let step = (high / low).powf(1.0 / (count - 1) as f32);
        let q = (step.sqrt() / (step - 1.0)) as f64;
        for k in 0..count {
            let f = (low * step.powi(k as i32)) as f64;
            let c = design::bandpass(f, q, rate as f64);
            let wide = design::bandpass(f, q.min(layout.carrier_q), rate as f64);
            for s in &mut self.input[k] {
                s.set(c);
            }
            for s in &mut self.carrier[k] {
                s.set(wide);
            }
        }
        self.count = count;
        self.attack = pole(5.0, rate);
        self.release = pole(20.0, rate);
        self.car_pole = pole(50.0, rate);
    }

    fn clear(&mut self) {
        for s in self
            .input
            .as_flattened_mut()
            .iter_mut()
            .chain(self.carrier.as_flattened_mut())
        {
            s.clear();
        }
        self.env = [0.0; MAX_BANDS];
        self.car = [0.0; MAX_BANDS];
    }

    #[inline]
    fn tick(&mut self, input: f32, carrier: f32) -> f32 {
        let mut out = 0.0;
        for k in 0..self.count {
            let [a, b] = &mut self.input[k];
            let level = b.tick(a.tick(input)).abs();
            let p = if level > self.env[k] {
                self.attack
            } else {
                self.release
            };
            self.env[k] = level + (self.env[k] - level) * p;
            let [a, b] = &mut self.carrier[k];
            let c = b.tick(a.tick(carrier));
            self.car[k] = c.abs() + (self.car[k] - c.abs()) * self.car_pole;
            out += c * self.env[k] / self.car[k].max(CARRIER_FLOOR);
        }
        out
    }
}

pub struct Whisper {
    seed: u64,
    rate: f32,
    random: Random,
    bank: Bank,
    mix: Smoothed,
}

impl Whisper {
    const BANDS: usize = 24;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
        ParamSpec::range("seed", 0.0, 65535.0, 0.0, ""),
    ];

    pub fn new() -> Self {
        let mut w = Whisper {
            seed: 0,
            rate: DEFAULT_RATE,
            random: Random(0),
            bank: Bank::new(),
            mix: Smoothed::new(1.0),
        };
        w.bank.design(Self::BANDS, &WHISPER, DEFAULT_RATE);
        w
    }
}

impl Effect for Whisper {
    basics!("whisper");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.mix.set(value),
            _ => self.seed = value.round() as u64,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.bank.design(Self::BANDS, &WHISPER, rate);
        self.bank.clear();
        self.random = Random(self.seed);
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let v = self.bank.tick(mono(*f), self.random.signed());
            *f = blend(*f, [v, v], self.mix.next());
        }
    }
}

/// `x` within PolyBLEP's two samples of a step: the correction that band-limits it.
#[inline]
fn blep(t: f32, dt: f32) -> f32 {
    if t < dt {
        let x = t / dt;
        x + x - x * x - 1.0
    } else if t > 1.0 - dt {
        let x = (t - 1.0) / dt;
        x * x + x + x + 1.0
    } else {
        0.0
    }
}

#[inline]
fn saw_at(t: f32, dt: f32) -> f32 {
    2.0 * t - 1.0 - blep(t, dt)
}

const PULSE_WIDTH: f32 = 0.25;
const CARRIERS: &[&str] = &["saw", "pulse", "side"];

/// The notes of a chord of the key, in Hz.
fn chord(values: &[f32; 5]) -> ([f32; 4], usize) {
    let [key, scale, degree, notes, octave] = values.map(|v| v.round() as i32);
    let s = Scale::new(key as usize, scale as usize);
    let tonic = 12 * (octave + 1) + key;
    let root = tonic + s.interval(tonic, degree - 1);
    let mut out = [0.0; 4];
    let count = notes.clamp(1, 4) as usize;
    for (k, f) in out.iter_mut().take(count).enumerate() {
        *f = freq((root + s.interval(root, 2 * k as i32)) as f32, 0.0);
    }
    (out, count)
}

/// A band-limited saw or pulse chord.
struct Synth {
    phases: [f32; 4],
    steps: [f32; 4],
    count: usize,
}

impl Synth {
    fn new() -> Self {
        Synth {
            phases: [0.0; 4],
            steps: [0.0; 4],
            count: 0,
        }
    }

    fn tune(&mut self, notes: &[f32; 4], count: usize, rate: f32) {
        for (s, f) in self.steps.iter_mut().zip(notes) {
            *s = (f / rate).min(0.49);
        }
        self.count = count;
    }

    fn reset(&mut self) {
        self.phases = [0.0; 4];
    }

    #[inline]
    fn tick(&mut self, pulse: bool) -> f32 {
        let mut out = 0.0;
        for (t, &dt) in self.phases.iter_mut().zip(&self.steps).take(self.count) {
            out += if pulse {
                let u = (*t + PULSE_WIDTH).fract();
                saw_at(*t, dt) - saw_at(u, dt)
            } else {
                saw_at(*t, dt)
            };
            *t += dt;
            if *t >= 1.0 {
                *t -= 1.0;
            }
        }
        out / (self.count as f32).sqrt()
    }
}

/// The carrier params a vocoder and a talkbox share, in the order of their specs.
struct Carrier {
    kind: usize,
    chord: [f32; 5],
    synth: Synth,
    side: Side,
}

impl Carrier {
    fn new() -> Self {
        Carrier {
            kind: 0,
            chord: [9.0, 1.0, 1.0, 3.0, 3.0],
            synth: Synth::new(),
            side: Side::new(),
        }
    }

    /// Sets param `i` of [`CARRIER_SPECS`].
    fn set(&mut self, i: usize, value: f32, rate: f32) {
        if i == 0 {
            self.kind = value.round() as usize;
        } else {
            self.chord[i - 1] = value;
        }
        self.tune(rate);
    }

    fn tune(&mut self, rate: f32) {
        let (notes, count) = chord(&self.chord);
        self.synth.tune(&notes, count, rate);
    }

    #[inline]
    fn tick(&mut self, k: usize) -> f32 {
        match self.kind {
            0 => self.synth.tick(false),
            1 => self.synth.tick(true),
            _ => mono(self.side.at(k)),
        }
    }
}

macro_rules! carrier_specs {
    () => {
        [
            ParamSpec::choice("carrier", CARRIERS),
            ParamSpec::choice("key", KEYS),
            ParamSpec::choice("scale", SCALES),
            ParamSpec::range("degree", 1.0, 7.0, 1.0, ""),
            ParamSpec::range("notes", 1.0, 4.0, 3.0, "notes"),
            ParamSpec::range("octave", 1.0, 6.0, 3.0, ""),
        ]
    };
}

pub struct Vocoder {
    bands: usize,
    rate: f32,
    carrier: Carrier,
    bank: Bank,
    mix: Smoothed,
}

impl Vocoder {
    const SPECS: &'static [ParamSpec] = &{
        let c = carrier_specs!();
        [
            ParamSpec::range("bands", 16.0, MAX_BANDS as f32, 24.0, "bands"),
            c[0],
            c[1],
            c[2],
            c[3],
            c[4],
            c[5],
            ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
        ]
    };

    pub fn new() -> Self {
        let mut v = Vocoder {
            bands: 24,
            rate: DEFAULT_RATE,
            carrier: Carrier::new(),
            bank: Bank::new(),
            mix: Smoothed::new(1.0),
        };
        v.carrier.tune(DEFAULT_RATE);
        v.bank.design(v.bands, &VOCODER, DEFAULT_RATE);
        v
    }
}

impl Effect for Vocoder {
    basics!("vocoder");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => {
                self.bands = value.round() as usize;
                self.bank.design(self.bands, &VOCODER, self.rate);
            }
            7 => self.mix.set(value),
            i => self.carrier.set(i - 1, value, self.rate),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.bank.design(self.bands, &VOCODER, rate);
        self.bank.clear();
        self.carrier.tune(rate);
        self.carrier.synth.reset();
        self.carrier.side.done();
        self.mix.reset(rate);
    }
    fn sidechain(&mut self, side: &[[f32; 2]]) {
        self.carrier.side.take(side);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for (k, f) in block.iter_mut().enumerate() {
            let c = self.carrier.tick(k);
            let v = self.bank.tick(mono(*f), c);
            *f = blend(*f, [v, v], self.mix.next());
        }
        self.carrier.side.done();
    }
}

const LPC_MAX_ORDER: usize = 32;
const LPC_WINDOW_MS: f32 = 30.0;
const EMPHASIS: f32 = 0.97;

/// Reflection coefficients from an autocorrelation (Levinson-Durbin), and the energy left
/// unpredicted; all zero for silence.
fn levinson(r: &[f64], k: &mut [f32], a: &mut [f64], scratch: &mut [f64]) -> f64 {
    let p = k.len();
    k.fill(0.0);
    if r[0] <= 1e-12 {
        return 0.0;
    }
    a.fill(0.0);
    a[0] = 1.0;
    let mut e = r[0];
    for i in 1..=p {
        let acc: f64 = r[i] + (1..i).map(|j| a[j] * r[i - j]).sum::<f64>();
        let ki = -acc / e;
        if ki.is_nan() || ki.abs() >= 1.0 {
            break;
        }
        scratch[..i].copy_from_slice(&a[..i]);
        for j in 1..i {
            a[j] = scratch[j] + ki * scratch[i - j];
        }
        a[i] = ki;
        k[i - 1] = ki as f32;
        e *= 1.0 - ki * ki;
    }
    e
}

pub struct Talkbox {
    rate: f32,
    order: usize,
    hop: usize,
    since: usize,
    carrier: Carrier,
    // the pre-emphasised input over the analysis window, oldest first from `pos`
    ring: Vec<f32>,
    pos: usize,
    window: Vec<f32>,
    frame: Vec<f32>,
    r: Vec<f64>,
    a: Vec<f64>,
    scratch: Vec<f64>,
    // reflection coefficients and gain: from, to, and now
    from: [f32; LPC_MAX_ORDER],
    to: [f32; LPC_MAX_ORDER],
    gain: [f32; 2],
    lattice: [f32; LPC_MAX_ORDER + 1],
    last_in: f32,
    last_carrier: f32,
    last_out: f32,
    carrier_power: f32,
    power_pole: f32,
    mix: Smoothed,
}

impl Talkbox {
    const SPECS: &'static [ParamSpec] = &{
        let c = carrier_specs!();
        [
            c[0],
            c[1],
            c[2],
            c[3],
            c[4],
            c[5],
            ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
        ]
    };

    pub fn new() -> Self {
        let mut t = Talkbox {
            rate: DEFAULT_RATE,
            order: 0,
            hop: 0,
            since: 0,
            carrier: Carrier::new(),
            ring: Vec::new(),
            pos: 0,
            window: Vec::new(),
            frame: Vec::new(),
            r: vec![0.0; LPC_MAX_ORDER + 1],
            a: vec![0.0; LPC_MAX_ORDER + 1],
            scratch: vec![0.0; LPC_MAX_ORDER + 1],
            from: [0.0; LPC_MAX_ORDER],
            to: [0.0; LPC_MAX_ORDER],
            gain: [0.0; 2],
            lattice: [0.0; LPC_MAX_ORDER + 1],
            last_in: 0.0,
            last_carrier: 0.0,
            last_out: 0.0,
            carrier_power: 0.0,
            power_pole: 0.0,
            mix: Smoothed::new(1.0),
        };
        t.carrier.tune(DEFAULT_RATE);
        t
    }

    /// A new analysis over the window: the next targets for the coefficients and the gain.
    fn analyse(&mut self) {
        let n = self.ring.len();
        for (i, (f, w)) in self.frame.iter_mut().zip(&self.window).enumerate() {
            *f = self.ring[(self.pos + i) % n] * w;
        }
        for (lag, r) in self.r.iter_mut().take(self.order + 1).enumerate() {
            *r = self.frame[..n - lag]
                .iter()
                .zip(&self.frame[lag..])
                .map(|(a, b)| (*a as f64) * (*b as f64))
                .sum();
        }
        // a whisker of white noise keeps the predictor well conditioned
        self.r[0] *= 1.0 + 1e-4;
        self.from = self.to;
        let energy = levinson(
            &self.r[..self.order + 1],
            &mut self.to[..self.order],
            &mut self.a[..self.order + 1],
            &mut self.scratch[..self.order + 1],
        );
        let weight: f32 = self.window.iter().map(|w| w * w).sum();
        self.gain = [self.gain[1], (energy as f32 / weight.max(1e-9)).sqrt()];
    }
}

impl Effect for Talkbox {
    basics!("talkbox");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            6 => self.mix.set(value),
            i => self.carrier.set(i, value, self.rate),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.order = ((rate / 2000.0).round() as usize + 8).clamp(8, LPC_MAX_ORDER);
        let n = frames(LPC_WINDOW_MS, rate).max(2 * self.order + 2);
        self.hop = (n / 2).max(1);
        self.since = 0;
        self.ring.clear();
        self.ring.resize(n, 0.0);
        self.pos = 0;
        self.window.clear();
        self.window
            .extend((0..n).map(|i| 0.5 - 0.5 * (2.0 * PI * (i as f32 + 0.5) / n as f32).cos()));
        self.frame.clear();
        self.frame.resize(n, 0.0);
        self.from = [0.0; LPC_MAX_ORDER];
        self.to = [0.0; LPC_MAX_ORDER];
        self.gain = [0.0; 2];
        self.lattice = [0.0; LPC_MAX_ORDER + 1];
        self.last_in = 0.0;
        self.last_carrier = 0.0;
        self.last_out = 0.0;
        self.carrier_power = 0.0;
        self.power_pole = pole(50.0, rate);
        self.carrier.tune(rate);
        self.carrier.synth.reset();
        self.carrier.side.done();
        self.mix.reset(rate);
    }
    fn sidechain(&mut self, side: &[[f32; 2]]) {
        self.carrier.side.take(side);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.ring.is_empty() {
            return;
        }
        finite(block);
        let p = self.order;
        for (k, f) in block.iter_mut().enumerate() {
            let x = mono(*f);
            self.ring[self.pos] = x - EMPHASIS * self.last_in;
            self.last_in = x;
            self.pos = (self.pos + 1) % self.ring.len();
            if self.since == 0 {
                self.analyse();
            }
            let t = self.since as f32 / self.hop as f32;
            self.since = (self.since + 1) % self.hop;
            let c = self.carrier.tick(k);
            let e = c - EMPHASIS * self.last_carrier;
            self.last_carrier = c;
            self.carrier_power = e * e + (self.carrier_power - e * e) * self.power_pole;
            let gain = self.gain[0] + t * (self.gain[1] - self.gain[0]);
            let excite = if self.carrier_power > 1e-12 {
                gain * e / self.carrier_power.sqrt()
            } else {
                0.0
            };
            // the all-pole lattice, from the last stage down
            let mut v = excite;
            for m in (0..p).rev() {
                let km = self.from[m] + t * (self.to[m] - self.from[m]);
                v -= km * self.lattice[m];
                self.lattice[m + 1] = self.lattice[m] + km * v;
            }
            self.lattice[0] = v;
            if self.lattice[0].abs() < 1e-30 {
                self.lattice[0] = 0.0;
            }
            let y = v + EMPHASIS * self.last_out;
            self.last_out = if y.abs() < 1e-30 { 0.0 } else { y };
            *f = blend(*f, [y, y], self.mix.next());
        }
        self.carrier.side.done();
    }
}

pub struct RingMod {
    freq: f32,
    rate: f32,
    phase: f64,
    mix: Smoothed,
}

impl RingMod {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("frequency", 1.0, 5000.0, 440.0, "Hz"),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        RingMod {
            freq: 440.0,
            rate: DEFAULT_RATE,
            phase: 0.0,
            mix: Smoothed::new(1.0),
        }
    }
}

impl Effect for RingMod {
    basics!("ring_mod");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.freq = value,
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.phase = 0.0;
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        let step = self.freq as f64 / self.rate as f64;
        for f in block {
            let m = (TAU * self.phase).sin() as f32;
            self.phase = (self.phase + step).fract();
            *f = blend(*f, [f[0] * m, f[1] * m], self.mix.next());
        }
    }
}

/// Olli Niemitalo's all-pass pair: outputs 90 degrees apart from 20 Hz to 20 kHz at 44.1 kHz.
const HILBERT: [[f64; 4]; 2] = [
    [0.6923878, 0.9360654322959, 0.9882295226860, 0.9987488452737],
    [
        0.4021921162426,
        0.8561710882420,
        0.9722909545651,
        0.9952884791278,
    ],
];

/// One chain of four second-order all-passes, y = a^2 (x + y[-2]) - x[-2].
#[derive(Clone, Copy)]
struct Chain {
    a: [f64; 4],
    x: [[f64; 2]; 4],
    y: [[f64; 2]; 4],
}

impl Chain {
    fn new(coefs: [f64; 4]) -> Self {
        Chain {
            a: coefs.map(|c| c * c),
            x: [[0.0; 2]; 4],
            y: [[0.0; 2]; 4],
        }
    }

    fn clear(&mut self) {
        self.x = [[0.0; 2]; 4];
        self.y = [[0.0; 2]; 4];
    }

    #[inline]
    fn tick(&mut self, input: f64) -> f64 {
        let mut v = input;
        for ((a, x), y) in self.a.iter().zip(&mut self.x).zip(&mut self.y) {
            let out = a * (v + y[1]) - x[1];
            *x = [v, x[0]];
            *y = [out, y[0]];
            if out.abs() < 1e-30 && y[1].abs() < 1e-30 {
                *y = [0.0; 2];
            }
            v = out;
        }
        v
    }
}

/// The analytic signal of a channel: the first chain a sample late, and the second.
#[derive(Clone, Copy)]
struct Hilbert {
    real: Chain,
    imag: Chain,
    late: f64,
}

impl Hilbert {
    fn new() -> Self {
        Hilbert {
            real: Chain::new(HILBERT[0]),
            imag: Chain::new(HILBERT[1]),
            late: 0.0,
        }
    }

    fn clear(&mut self) {
        self.real.clear();
        self.imag.clear();
        self.late = 0.0;
    }

    #[inline]
    fn tick(&mut self, x: f32) -> (f64, f64) {
        let re = std::mem::replace(&mut self.late, self.real.tick(x as f64));
        (re, self.imag.tick(x as f64))
    }
}

pub struct FreqShift {
    shift: f32,
    rate: f32,
    phase: f64,
    hilbert: [Hilbert; 2],
    mix: Smoothed,
}

impl FreqShift {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("shift", -2000.0, 2000.0, 100.0, "Hz"),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        FreqShift {
            shift: 100.0,
            rate: DEFAULT_RATE,
            phase: 0.0,
            hilbert: [Hilbert::new(); 2],
            mix: Smoothed::new(1.0),
        }
    }
}

impl Effect for FreqShift {
    basics!("freq_shift");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.shift = value,
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.phase = 0.0;
        for h in &mut self.hilbert {
            h.clear();
        }
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        let step = self.shift as f64 / self.rate as f64;
        for f in block {
            let (s, c) = (TAU * self.phase).sin_cos();
            self.phase = (self.phase + step).rem_euclid(1.0);
            let mut wet = [0.0f32; 2];
            for (w, (h, x)) in wet.iter_mut().zip(self.hilbert.iter_mut().zip(*f)) {
                let (re, im) = h.tick(x);
                *w = (re * c + im * s) as f32;
            }
            *f = blend(*f, wet, self.mix.next());
        }
    }
}

const COMB_MAX_MS: f32 = 50.0;

pub struct Comb {
    rate: f32,
    line: Line,
    delay: Smoothed,
    delay_ms: f32,
    feedback: Smoothed,
    mix: Smoothed,
}

impl Comb {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("delay", 0.1, COMB_MAX_MS, 5.0, "ms"),
        ParamSpec::range("feedback", -0.95, 0.95, 0.7, ""),
        ParamSpec::range("mix", 0.0, 1.0, 0.5, ""),
    ];

    pub fn new() -> Self {
        Comb {
            rate: DEFAULT_RATE,
            line: Line::new(),
            delay: Smoothed::new(5.0 * 0.001 * DEFAULT_RATE),
            delay_ms: 5.0,
            feedback: Smoothed::new(0.7),
            mix: Smoothed::new(0.5),
        }
    }

    fn delay_frames(&self) -> f32 {
        (self.delay_ms * 0.001 * self.rate).max(1.0)
    }
}

impl Effect for Comb {
    basics!("comb");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => {
                self.delay_ms = value;
                self.delay.set(self.delay_frames());
            }
            1 => self.feedback.set(value),
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.line.reset(frames(COMB_MAX_MS, rate) + 4);
        self.delay.set(self.delay_frames());
        for s in [&mut self.delay, &mut self.feedback, &mut self.mix] {
            s.reset(rate);
        }
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.line.ready() {
            return;
        }
        finite(block);
        for f in block {
            let (fb, mix) = (self.feedback.next(), self.mix.next());
            // the line's newest frame is y[n - 1]: a delay of d frames reads d - 1 back
            let back = self.delay.next() - 1.0;
            let y = [
                f[0] + fb * self.line.at(back, 0),
                f[1] + fb * self.line.at(back, 1),
            ];
            self.line.push(y);
            let norm = 1.0 - fb.abs();
            *f = blend(*f, [y[0] * norm, y[1] * norm], mix);
        }
    }
}

/// The lowest note a resonator can ring at, in Hz, which sizes its lines.
const LOWEST_NOTE: f32 = 30.0;

/// A feedback comb tuned to a note, ringing to -60 dB in the resonator's decay.
struct Tuned {
    line: Vec<[f32; 2]>,
    pos: usize,
    delay: f32,
    feedback: f32,
}

impl Tuned {
    fn new() -> Self {
        Tuned {
            line: Vec::new(),
            pos: 0,
            delay: 1.0,
            feedback: 0.0,
        }
    }

    fn reset(&mut self, rate: f32) {
        self.line.clear();
        self.line
            .resize((rate / LOWEST_NOTE) as usize + 4, [0.0; 2]);
        self.pos = 0;
    }

    fn tune(&mut self, hz: f32, decay: f32, rate: f32) {
        let most = self.line.len().saturating_sub(3).max(1) as f32;
        self.delay = (rate / hz).clamp(1.0, most);
        self.feedback = 10f32.powf(-3.0 * self.delay / (decay * rate));
    }

    #[inline]
    fn tick(&mut self, x: [f32; 2]) -> [f32; 2] {
        let n = self.line.len();
        // y[n - d], linearly between frames; pos is where y[n] goes
        let back = self.pos + n - 1 - (self.delay - 1.0).floor() as usize;
        let t = (self.delay - 1.0).fract();
        let (a, b) = (self.line[back % n], self.line[(back + n - 1) % n]);
        let mut y = [0.0; 2];
        for c in 0..2 {
            let delayed = a[c] + t * (b[c] - a[c]);
            y[c] = x[c] + self.feedback * delayed;
            if y[c].abs() < 1e-30 {
                y[c] = 0.0;
            }
        }
        self.line[self.pos] = y;
        self.pos = (self.pos + 1) % n;
        let norm = 1.0 - self.feedback;
        [y[0] * norm, y[1] * norm]
    }
}

pub struct Resonator {
    values: [f32; 7],
    rate: f32,
    count: usize,
    combs: [Tuned; 4],
    dc: [DcBlock; 2],
    mix: Smoothed,
}

impl Resonator {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::choice("key", KEYS),
        ParamSpec::choice("scale", SCALES),
        ParamSpec::range("degree", 1.0, 7.0, 1.0, ""),
        ParamSpec::range("notes", 1.0, 4.0, 3.0, "notes"),
        ParamSpec::range("octave", 1.0, 6.0, 3.0, ""),
        ParamSpec::range("decay", 0.1, 10.0, 1.5, "s"),
        ParamSpec::range("mix", 0.0, 1.0, 0.5, ""),
    ];

    pub fn new() -> Self {
        let values: [f32; 7] = defaults(Self::SPECS);
        Resonator {
            values,
            rate: DEFAULT_RATE,
            count: 0,
            combs: [Tuned::new(), Tuned::new(), Tuned::new(), Tuned::new()],
            dc: [DcBlock::new(); 2],
            mix: Smoothed::new(values[6]),
        }
    }

    fn tune(&mut self) {
        let v = &self.values;
        let (notes, count) = chord(&[v[0], v[1], v[2], v[3], v[4]]);
        for (c, hz) in self.combs.iter_mut().zip(notes) {
            c.tune(hz, v[5], self.rate);
        }
        self.count = count;
    }
}

impl Effect for Resonator {
    basics!("resonator");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        self.values[i] = value;
        if i == 6 {
            self.mix.set(value);
        } else if !self.combs[0].line.is_empty() {
            self.tune();
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        for c in &mut self.combs {
            c.reset(rate);
        }
        for d in &mut self.dc {
            d.reset(rate);
        }
        self.tune();
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.combs[0].line.is_empty() {
            return;
        }
        finite(block);
        let norm = 1.0 / (self.count as f32).sqrt();
        for f in block {
            let x = [self.dc[0].tick(f[0]), self.dc[1].tick(f[1])];
            let mut wet = [0.0f32; 2];
            for c in self.combs.iter_mut().take(self.count) {
                let y = c.tick(x);
                wet[0] += y[0] * norm;
                wet[1] += y[1] * norm;
            }
            *f = blend(*f, wet, self.mix.next());
        }
    }
}

const EXCITE_DRIVE: f32 = 4.0;
const EXCITE_BIAS: f32 = 0.2;

pub struct Exciter {
    freq: f32,
    rate: f32,
    before: Cascade<2>,
    after: Cascade<2>,
    amount: Smoothed,
}

impl Exciter {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("frequency", 1000.0, 12000.0, 3000.0, "Hz"),
        ParamSpec::range("amount", 0.0, 1.0, 0.3, ""),
    ];

    pub fn new() -> Self {
        let mut e = Exciter {
            freq: 3000.0,
            rate: DEFAULT_RATE,
            before: Cascade::new(),
            after: Cascade::new(),
            amount: Smoothed::new(0.3),
        };
        e.design();
        e
    }

    fn design(&mut self) {
        let f = under_nyquist(self.freq, self.rate);
        let c = design::highpass(f, std::f64::consts::FRAC_1_SQRT_2, self.rate as f64);
        for i in 0..2 {
            self.before.set(i, c);
            self.after.set(i, c);
        }
    }

    /// What tanh adds to a straight line through the same point: the harmonics only.
    #[inline]
    fn bend(h: f32) -> f32 {
        let slope = EXCITE_DRIVE * (1.0 - EXCITE_BIAS.tanh().powi(2));
        (EXCITE_DRIVE * h + EXCITE_BIAS).tanh() - EXCITE_BIAS.tanh() - slope * h
    }
}

impl Effect for Exciter {
    basics!("exciter");
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => {
                self.freq = value;
                self.design();
            }
            _ => self.amount.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.design();
        self.before.clear();
        self.after.clear();
        self.amount.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for f in block {
            let h = self.before.tick(*f);
            let added = self.after.tick([Self::bend(h[0]), Self::bend(h[1])]);
            let amount = self.amount.next();
            f[0] += amount * added[0];
            f[1] += amount * added[1];
        }
    }
}
