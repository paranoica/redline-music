//! The effects of the rack.

use crate::design::{self, Sos};
use crate::effect::{check, Effect, ParamError, ParamSpec};
use std::f32::consts::{FRAC_1_SQRT_2, FRAC_PI_2};

pub const EFFECTS: &[&str] = &[
    "gain", "pan", "width", "eq", "filter", "saturate", "bitcrush",
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

    fn process(&mut self, block: &mut [[f32; 2]]) {
        let c = self.c;
        for frame in block {
            for (v, z) in frame.iter_mut().zip(self.z.iter_mut()) {
                let x = *v as f64;
                let y = c[0] * x + z[0];
                z[0] = c[1] * x - c[4] * y + z[1];
                z[1] = c[2] * x - c[5] * y;
                *v = y as f32;
            }
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
    gain: f32,
}

specs!(Gain, &[ParamSpec::range("db", -60.0, 24.0, 0.0, "dB")]);

impl Gain {
    fn new() -> Self {
        Gain { gain: 1.0 }
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
        self.gain = amplitude(value);
        Ok(())
    }
    fn reset(&mut self, _rate: f32) {}
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            f[0] *= self.gain;
            f[1] *= self.gain;
        }
    }
}

/// Balance: the far side is turned down along a quarter cosine, the near side stays.
pub struct Pan {
    gains: [f32; 2],
}

specs!(Pan, &[ParamSpec::range("pan", -1.0, 1.0, 0.0, "")]);

impl Pan {
    fn new() -> Self {
        Pan { gains: [1.0, 1.0] }
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
        self.gains = if value >= 0.0 { [far, 1.0] } else { [1.0, far] };
        Ok(())
    }
    fn reset(&mut self, _rate: f32) {}
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            f[0] *= self.gains[0];
            f[1] *= self.gains[1];
        }
    }
}

/// Mid/side: the side scaled by the width, the mid kept.
pub struct Width {
    width: f32,
}

specs!(Width, &[ParamSpec::range("width", 0.0, 2.0, 1.0, "x")]);

impl Width {
    fn new() -> Self {
        Width { width: 1.0 }
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
        self.width = value;
        Ok(())
    }
    fn reset(&mut self, _rate: f32) {}
    fn process(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            let mid = 0.5 * (f[0] + f[1]);
            let side = 0.5 * (f[0] - f[1]) * self.width;
            *f = [mid + side, mid - side];
        }
    }
}

/// One RBJ band.
pub struct Eq {
    values: [f32; 4],
    rate: f32,
    filter: Biquad,
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
        let mut eq = Eq {
            values: defaults(Self::SPECS),
            rate: DEFAULT_RATE,
            filter: Biquad::new(),
        };
        eq.design();
        eq
    }

    fn design(&mut self) {
        let [kind, freq, gain, q] = self.values;
        let (freq, gain, q, rate) = (
            under_nyquist(freq, self.rate),
            gain as f64,
            q as f64,
            self.rate as f64,
        );
        self.filter.c = match kind.round() as usize {
            0 => design::peaking(freq, gain, q, rate),
            1 => design::low_shelf(freq, gain, q, rate),
            2 => design::high_shelf(freq, gain, q, rate),
            3 => design::lowpass(freq, q, rate),
            4 => design::highpass(freq, q, rate),
            _ => design::bandpass(freq, q, rate),
        };
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
        self.values[check(self.name(), Self::SPECS, name, value)?] = value;
        self.design();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.filter = Biquad::new();
        self.design();
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        self.filter.process(block);
    }
}

/// A resonant low, high or band pass.
pub struct Filter {
    values: [f32; 4],
    rate: f32,
    filter: Biquad,
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
        let mut f = Filter {
            values: defaults(Self::SPECS),
            rate: DEFAULT_RATE,
            filter: Biquad::new(),
        };
        f.design();
        f
    }

    fn design(&mut self) {
        let [mode, cutoff, q, _] = self.values;
        let (cutoff, q, rate) = (under_nyquist(cutoff, self.rate), q as f64, self.rate as f64);
        self.filter.c = match mode.round() as usize {
            0 => design::lowpass(cutoff, q, rate),
            1 => design::highpass(cutoff, q, rate),
            _ => design::bandpass(cutoff, q, rate),
        };
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
        self.values[check(self.name(), Self::SPECS, name, value)?] = value;
        self.design();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.filter = Biquad::new();
        self.design();
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        self.filter.process(block);
    }
}

/// tanh of the driven signal, mixed with the dry one.
pub struct Saturate {
    drive: f32,
    mix: f32,
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
            drive: 1.0,
            mix: 1.0,
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
            0 => self.drive = amplitude(value),
            _ => self.mix = value,
        }
        Ok(())
    }
    fn reset(&mut self, _rate: f32) {}
    fn process(&mut self, block: &mut [[f32; 2]]) {
        let (drive, wet, dry) = (self.drive, self.mix, 1.0 - self.mix);
        for f in block {
            for v in f.iter_mut() {
                *v = wet * (drive * *v).tanh() + dry * *v;
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
