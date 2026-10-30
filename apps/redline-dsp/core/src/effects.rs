//! The effects of the rack.

use crate::colour;
use crate::design::{self, Sos};
use crate::dynamics;
use crate::effect::{check, Effect, ParamError, ParamSpec, Smoothed};
use crate::space;
use crate::time;
use crate::voice;
use std::f32::consts::{FRAC_1_SQRT_2, FRAC_PI_2};

pub const EFFECTS: &[&str] = &[
    "gain",
    "pan",
    "width",
    "eq",
    "filter",
    "saturate",
    "bitcrush",
    "pitch",
    "formant",
    "octaver",
    "harmonizer",
    "unison",
    "vibrato",
    "drift",
    "robot",
    "tune",
    "varispeed",
    "timestretch",
    "tape_stop",
    "tape_start",
    "reverse",
    "freeze",
    "granular",
    "stutter",
    "chop",
    "trance_gate",
    "room",
    "plate",
    "hall",
    "shimmer",
    "gated_reverb",
    "reverse_reverb",
    "delay",
    "tape_delay",
    "throws",
    "chorus",
    "flanger",
    "phaser",
    "doubler",
    "autopan",
    "compressor",
    "gate",
    "deesser",
    "limiter",
    "multiband",
    "duck",
    "transient",
    "distort",
    "downsample",
    "telephone",
    "radio",
    "lofi",
    "underwater",
    "whisper",
    "vocoder",
    "talkbox",
    "ring_mod",
    "freq_shift",
    "comb",
    "resonator",
    "exciter",
];

pub fn make(name: &str) -> Option<Box<dyn Effect>> {
    Some(match name {
        "gain" => Box::new(Gain::new()),
        "pan" => Box::new(Pan::new()),
        "width" => Box::new(Width::new()),
        "eq" => Box::new(Eq::new()),
        "filter" => Box::new(Filter::new()),
        "saturate" => Box::new(Saturate::new()),
        "bitcrush" => Box::new(Bitcrush::new()),
        "pitch" => Box::new(voice::Pitch::new()),
        "formant" => Box::new(voice::Formant::new()),
        "octaver" => Box::new(voice::Octaver::new()),
        "harmonizer" => Box::new(voice::Harmonizer::new()),
        "unison" => Box::new(voice::Unison::new()),
        "vibrato" => Box::new(voice::Vibrato::new()),
        "drift" => Box::new(voice::Drift::new()),
        "robot" => Box::new(voice::Robot::new()),
        "tune" => Box::new(voice::Tune::new()),
        "varispeed" => Box::new(time::Varispeed::new()),
        "timestretch" => Box::new(time::Timestretch::new()),
        "tape_stop" => Box::new(time::TapeMotion::stop()),
        "tape_start" => Box::new(time::TapeMotion::start()),
        "reverse" => Box::new(time::Reverse::new()),
        "freeze" => Box::new(time::Freeze::new()),
        "granular" => Box::new(time::Granular::new()),
        "stutter" => Box::new(time::Stutter::new()),
        "chop" => Box::new(time::Chop::new()),
        "trance_gate" => Box::new(time::TranceGate::new()),
        "room" => Box::new(space::room()),
        "plate" => Box::new(space::plate()),
        "hall" => Box::new(space::hall()),
        "shimmer" => Box::new(space::shimmer()),
        "gated_reverb" => Box::new(space::gated_reverb()),
        "reverse_reverb" => Box::new(space::ReverseReverb::new()),
        "delay" => Box::new(space::Delay::new()),
        "tape_delay" => Box::new(space::TapeDelay::new()),
        "throws" => Box::new(space::Throws::new()),
        "chorus" => Box::new(space::Chorus::new()),
        "flanger" => Box::new(space::Flanger::new()),
        "phaser" => Box::new(space::Phaser::new()),
        "doubler" => Box::new(space::Doubler::new()),
        "autopan" => Box::new(space::Autopan::new()),
        "compressor" => Box::new(dynamics::Compressor::new()),
        "gate" => Box::new(dynamics::Gate::new()),
        "deesser" => Box::new(dynamics::Deesser::new()),
        "limiter" => Box::new(dynamics::Limiter::new()),
        "multiband" => Box::new(dynamics::Multiband::new()),
        "duck" => Box::new(dynamics::Duck::new()),
        "transient" => Box::new(dynamics::Transient::new()),
        "distort" => Box::new(colour::Distort::new()),
        "downsample" => Box::new(colour::Downsample::new()),
        "telephone" => Box::new(colour::Telephone::new()),
        "radio" => Box::new(colour::Radio::new()),
        "lofi" => Box::new(colour::Lofi::new()),
        "underwater" => Box::new(colour::Underwater::new()),
        "whisper" => Box::new(colour::Whisper::new()),
        "vocoder" => Box::new(colour::Vocoder::new()),
        "talkbox" => Box::new(colour::Talkbox::new()),
        "ring_mod" => Box::new(colour::RingMod::new()),
        "freq_shift" => Box::new(colour::FreqShift::new()),
        "comb" => Box::new(colour::Comb::new()),
        "resonator" => Box::new(colour::Resonator::new()),
        "exciter" => Box::new(colour::Exciter::new()),
        _ => return None,
    })
}

const DEFAULT_RATE: f32 = 48000.0;

fn amplitude(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

/// Defaults of up to four params, in the order of their specs.
fn defaults(specs: &[ParamSpec]) -> [f32; 4] {
    let mut v = [0.0; 4];
    for (slot, s) in v.iter_mut().zip(specs) {
        *slot = s.default;
    }
    v
}

/// One second-order section per channel, direct form II transposed, in double precision.
#[derive(Clone, Copy)]
struct Biquad {
    c: Sos,
    z: [[f64; 2]; 2],
}

impl Biquad {
    const PASS: Sos = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0];

    fn new() -> Self {
        Biquad {
            c: Self::PASS,
            z: [[0.0; 2]; 2],
        }
    }

    #[inline]
    fn tick(&mut self, frame: [f32; 2]) -> [f32; 2] {
        let c = self.c;
        let mut out = [0.0; 2];
        for ((v, z), o) in frame.iter().zip(self.z.iter_mut()).zip(&mut out) {
            // NaN or infinity would stay in the state for good
            let x = if v.is_finite() { *v as f64 } else { 0.0 };
            let y = c[0] * x + z[0];
            z[0] = c[1] * x - c[4] * y + z[1];
            z[1] = c[2] * x - c[5] * y;
            // A high-q low pole rings for minutes; stop it at 1e-30 (-600 dB) rather than creep
            // through subnormals. Both values at once: zeroing one alone can feed a slow
            // resonance more than it decays per sample, and it rings on for good.
            if z[0].abs() < 1e-30 && z[1].abs() < 1e-30 {
                *z = [0.0; 2];
            }
            *o = y as f32;
        }
        out
    }
}

/// Frames between redesigns of a band while its params glide.
const REDESIGN: usize = 32;
/// A change of filter kind crossfades from the old filter over this long, in seconds.
const CROSSFADE: f32 = 0.01;

/// A biquad following its params without clicks: frequency and q glide on a log scale and
/// gain in dB, redesigned every few frames while they move. A new kind has no in-between
/// with the old one, so the old filter keeps running and is crossfaded out.
struct Band {
    kind: usize,
    freq: Smoothed,
    gain: Smoothed,
    q: Smoothed,
    rate: f32,
    filter: Biquad,
    old: Biquad,
    fade: usize,
    fade_len: usize,
    countdown: usize,
    design: fn(usize, f64, f64, f64, f64) -> Sos,
}

impl Band {
    fn new(
        kind: usize,
        freq: f32,
        gain: f32,
        q: f32,
        design: fn(usize, f64, f64, f64, f64) -> Sos,
    ) -> Self {
        let mut b = Band {
            kind,
            freq: Smoothed::new(freq.log2()),
            gain: Smoothed::new(gain),
            q: Smoothed::new(q.log2()),
            rate: DEFAULT_RATE,
            filter: Biquad::new(),
            old: Biquad::new(),
            fade: 0,
            fade_len: 1,
            countdown: 0,
            design,
        };
        b.redesign();
        b
    }

    fn redesign(&mut self) {
        let freq = under_nyquist(self.freq.value().exp2(), self.rate);
        self.filter.c = (self.design)(
            self.kind,
            freq,
            self.gain.value() as f64,
            self.q.value().exp2() as f64,
            self.rate as f64,
        );
    }

    fn set_kind(&mut self, kind: usize) {
        if kind == self.kind {
            return;
        }
        self.kind = kind;
        self.old = self.filter;
        self.fade = self.fade_len;
        self.redesign();
    }

    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        for s in [&mut self.freq, &mut self.gain, &mut self.q] {
            s.reset(rate);
        }
        self.filter = Biquad::new();
        self.fade = 0;
        self.fade_len = ((CROSSFADE * rate) as usize).max(1);
        self.countdown = 0;
        self.redesign();
    }

    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            if self.countdown == 0 {
                self.countdown = REDESIGN;
                if !(self.freq.settled() && self.gain.settled() && self.q.settled()) {
                    for s in [&mut self.freq, &mut self.gain, &mut self.q] {
                        s.skip(REDESIGN);
                    }
                    self.redesign();
                }
            }
            self.countdown -= 1;
            let y = self.filter.tick(*f);
            *f = if self.fade == 0 {
                y
            } else {
                let o = self.old.tick(*f);
                let t = self.fade as f32 / self.fade_len as f32;
                self.fade -= 1;
                [y[0] + (o[0] - y[0]) * t, y[1] + (o[1] - y[1]) * t]
            };
        }
    }
}

/// Keeps a corner frequency under the Nyquist frequency of the rate.
fn under_nyquist(freq: f32, rate: f32) -> f64 {
    freq.min(0.49 * rate) as f64
}

macro_rules! specs {
    ($ty:ident, $specs:expr) => {
        impl $ty {
            const SPECS: &'static [ParamSpec] = $specs;
        }
    };
}

pub struct Gain {
    gain: Smoothed,
}

specs!(Gain, &[ParamSpec::range("db", -60.0, 24.0, 0.0, "dB")]);

impl Gain {
    fn new() -> Self {
        Gain {
            gain: Smoothed::new(1.0),
        }
    }
}

impl Effect for Gain {
    fn name(&self) -> &'static str {
        "gain"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        check(self.name(), Self::SPECS, name, value)?;
        self.gain.set(amplitude(value));
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.gain.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            let g = self.gain.next();
            f[0] *= g;
            f[1] *= g;
        }
    }
}

/// Balance: the far side is turned down along a quarter cosine, the near side stays.
pub struct Pan {
    gains: [Smoothed; 2],
}

specs!(Pan, &[ParamSpec::range("pan", -1.0, 1.0, 0.0, "")]);

impl Pan {
    fn new() -> Self {
        Pan {
            gains: [Smoothed::new(1.0); 2],
        }
    }
}

impl Effect for Pan {
    fn name(&self) -> &'static str {
        "pan"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        check(self.name(), Self::SPECS, name, value)?;
        let far = (value.abs() * FRAC_PI_2).cos();
        let gains = if value >= 0.0 { [far, 1.0] } else { [1.0, far] };
        for (s, g) in self.gains.iter_mut().zip(gains) {
            s.set(g);
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        for s in &mut self.gains {
            s.reset(rate);
        }
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            f[0] *= self.gains[0].next();
            f[1] *= self.gains[1].next();
        }
    }
}

/// Mid/side: the side scaled by the width, the mid kept.
pub struct Width {
    width: Smoothed,
}

specs!(Width, &[ParamSpec::range("width", 0.0, 2.0, 1.0, "x")]);

impl Width {
    fn new() -> Self {
        Width {
            width: Smoothed::new(1.0),
        }
    }
}

impl Effect for Width {
    fn name(&self) -> &'static str {
        "width"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        check(self.name(), Self::SPECS, name, value)?;
        self.width.set(value);
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.width.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            let mid = 0.5 * (f[0] + f[1]);
            let side = 0.5 * (f[0] - f[1]) * self.width.next();
            *f = [mid + side, mid - side];
        }
    }
}

/// One RBJ band.
pub struct Eq {
    band: Band,
}

fn eq_design(kind: usize, freq: f64, gain: f64, q: f64, rate: f64) -> Sos {
    match kind {
        0 => design::peaking(freq, gain, q, rate),
        1 => design::low_shelf(freq, gain, q, rate),
        2 => design::high_shelf(freq, gain, q, rate),
        3 => design::lowpass(freq, q, rate),
        4 => design::highpass(freq, q, rate),
        _ => design::bandpass(freq, q, rate),
    }
}

fn filter_design(mode: usize, freq: f64, _gain: f64, q: f64, rate: f64) -> Sos {
    match mode {
        0 => design::lowpass(freq, q, rate),
        1 => design::highpass(freq, q, rate),
        _ => design::bandpass(freq, q, rate),
    }
}

specs!(
    Eq,
    &[
        ParamSpec::choice(
            "kind",
            &[
                "peak",
                "lowshelf",
                "highshelf",
                "lowpass",
                "highpass",
                "bandpass"
            ]
        ),
        ParamSpec::range("freq", 20.0, 20000.0, 1000.0, "Hz"),
        ParamSpec::range("gain", -24.0, 24.0, 0.0, "dB"),
        ParamSpec::range("q", 0.1, 18.0, FRAC_1_SQRT_2, ""),
    ]
);

impl Eq {
    fn new() -> Self {
        let [kind, freq, gain, q] = defaults(Self::SPECS);
        Eq {
            band: Band::new(kind as usize, freq, gain, q, eq_design),
        }
    }
}

impl Effect for Eq {
    fn name(&self) -> &'static str {
        "eq"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        let b = &mut self.band;
        match i {
            0 => b.set_kind(value.round() as usize),
            1 => b.freq.set(value.log2()),
            2 => b.gain.set(value),
            _ => b.q.set(value.log2()),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.band.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        self.band.process(block);
    }
}

/// A resonant low, high or band pass.
pub struct Filter {
    band: Band,
}

specs!(
    Filter,
    &[
        ParamSpec::choice("mode", &["lp", "hp", "bp"]),
        ParamSpec::range("cutoff", 20.0, 20000.0, 1000.0, "Hz"),
        ParamSpec::range("resonance", 0.1, 18.0, FRAC_1_SQRT_2, "q"),
    ]
);

impl Filter {
    fn new() -> Self {
        let [mode, cutoff, q, _] = defaults(Self::SPECS);
        Filter {
            band: Band::new(mode as usize, cutoff, 0.0, q, filter_design),
        }
    }
}

impl Effect for Filter {
    fn name(&self) -> &'static str {
        "filter"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        let b = &mut self.band;
        match i {
            0 => b.set_kind(value.round() as usize),
            1 => b.freq.set(value.log2()),
            _ => b.q.set(value.log2()),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.band.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        self.band.process(block);
    }
}

/// tanh of the driven signal, mixed with the dry one.
pub struct Saturate {
    drive: Smoothed,
    mix: Smoothed,
}

specs!(
    Saturate,
    &[
        ParamSpec::range("drive", 0.0, 48.0, 0.0, "dB"),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ]
);

impl Saturate {
    fn new() -> Self {
        Saturate {
            drive: Smoothed::new(1.0),
            mix: Smoothed::new(1.0),
        }
    }
}

impl Effect for Saturate {
    fn name(&self) -> &'static str {
        "saturate"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.drive.set(amplitude(value)),
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.drive.reset(rate);
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            let (drive, wet) = (self.drive.next(), self.mix.next());
            for v in f.iter_mut() {
                *v = wet * (drive * *v).tanh() + (1.0 - wet) * *v;
            }
        }
    }
}

/// Fewer bits, and each kept sample held for `hold` samples.
pub struct Bitcrush {
    levels: f32,
    hold: usize,
    count: usize,
    held: [f32; 2],
}

specs!(
    Bitcrush,
    &[
        ParamSpec::range("bits", 1.0, 24.0, 24.0, "bits"),
        ParamSpec::range("hold", 1.0, 64.0, 1.0, "samples"),
    ]
);

impl Bitcrush {
    fn new() -> Self {
        Bitcrush {
            levels: 2f32.powi(23),
            hold: 1,
            count: 0,
            held: [0.0; 2],
        }
    }
}

impl Effect for Bitcrush {
    fn name(&self) -> &'static str {
        "bitcrush"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.levels = 2f32.powi(value.round() as i32 - 1),
            _ => self.hold = value.round() as usize,
        }
        Ok(())
    }
    fn reset(&mut self, _rate: f32) {
        self.count = 0;
        self.held = [0.0; 2];
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            if self.count == 0 {
                self.held = f.map(|v| (v * self.levels).round() / self.levels);
            }
            *f = self.held;
            self.count += 1;
            if self.count >= self.hold {
                self.count = 0;
            }
        }
    }
}
