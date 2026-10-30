//! The pitch effects of the voice lab. Shifting goes through signalsmith-stretch; the
//! stretchers are created in `reset`, so an effect that was never reset passes sound through.

use crate::effect::{check, finite, Effect, ParamError, ParamSpec, Smoothed, MIN_RATE};
use crate::pitch::{freq, midi, Scale, Tracker, KEYS, SCALES};
use crate::stretch::Stretch;
use std::f32::consts::{FRAC_PI_4, LN_2, PI};

/// Frames per call into a stretcher; scratch buffers are this long.
const CHUNK: usize = 256;
const ON_OFF: &[&str] = &["off", "on"];

fn ratio(semitones: f32) -> f32 {
    2f32.powf(semitones / 12.0)
}

/// A stretcher made by `reset` and kept while the rate stays.
struct Shifter {
    stretch: Option<Stretch>,
    rate: f32,
    channels: usize,
    seed: u32,
}

impl Shifter {
    fn new(channels: usize, seed: u32) -> Self {
        Shifter {
            stretch: None,
            rate: 0.0,
            channels,
            seed,
        }
    }

    /// Under [`MIN_RATE`] there is no stretcher, and the effect passes sound through.
    fn reset(&mut self, rate: f32) {
        if rate.is_nan() || rate < MIN_RATE {
            self.stretch = None;
            self.rate = 0.0;
            return;
        }
        match &mut self.stretch {
            Some(s) if self.rate == rate => s.reset(),
            _ => {
                self.stretch = Some(Stretch::new(self.channels, rate, self.seed));
                self.rate = rate;
            }
        }
    }

    fn latency(&self) -> usize {
        self.stretch.as_ref().map_or(0, Stretch::latency)
    }

    fn input_latency(&self) -> usize {
        self.stretch.as_ref().map_or(0, Stretch::input_latency)
    }

    fn transpose(&mut self, factor: f32) {
        if let Some(s) = &mut self.stretch {
            s.set_transpose(factor);
        }
    }

    fn formant(&mut self, factor: f32, compensate: bool, base: f32) {
        if let Some(s) = &mut self.stretch {
            s.set_formant(factor, compensate, base);
        }
    }

    /// Stereo frames through the stretcher in place; `scratch` keeps a copy of the input.
    fn process_stereo(&mut self, scratch: &mut [f32], block: &mut [[f32; 2]]) {
        let Some(s) = &mut self.stretch else { return };
        for chunk in block.chunks_mut(CHUNK) {
            let input = &mut scratch[..chunk.len() * 2];
            input.copy_from_slice(chunk.as_flattened());
            s.process(input, chunk.as_flattened_mut());
        }
    }

    fn process(&mut self, input: &[f32], output: &mut [f32]) {
        if let Some(s) = &mut self.stretch {
            s.process(input, output);
        }
    }
}

/// The dry signal held back by the wet path's latency.
struct Delay {
    buf: Vec<[f32; 2]>,
    pos: usize,
}

impl Delay {
    fn new() -> Self {
        Delay {
            buf: Vec::new(),
            pos: 0,
        }
    }

    fn reset(&mut self, len: usize) {
        self.buf.clear();
        self.buf.resize(len, [0.0; 2]);
        self.pos = 0;
    }

    fn tick(&mut self, x: [f32; 2]) -> [f32; 2] {
        if self.buf.is_empty() {
            return x;
        }
        let y = std::mem::replace(&mut self.buf[self.pos], x);
        self.pos += 1;
        if self.pos == self.buf.len() {
            self.pos = 0;
        }
        y
    }
}

fn mono(f: [f32; 2]) -> f32 {
    0.5 * (f[0] + f[1])
}

/// Frames to take next: up to a chunk, cut where the tracker completes a detection.
fn next_chunk(remaining: usize, tracker: &Tracker) -> usize {
    remaining.min(CHUNK).min(tracker.until_detection().max(1))
}

/// Transposes by semitones and cents, formants moved along or kept.
pub struct Pitch {
    semitones: f32,
    cents: f32,
    preserve: bool,
    steered: Steered,
}

impl Pitch {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("semitones", -24.0, 24.0, 0.0, "st"),
        ParamSpec::range("cents", -100.0, 100.0, 0.0, "cents"),
        ParamSpec::choice("preserve", ON_OFF),
    ];

    pub fn new() -> Self {
        Pitch {
            semitones: 0.0,
            cents: 0.0,
            preserve: false,
            steered: Steered::new(1),
        }
    }

    fn apply(&mut self) {
        self.steered
            .shifter
            .transpose(ratio(self.semitones + self.cents / 100.0));
        self.steered.set_formant(1.0, self.preserve);
    }
}

impl Effect for Pitch {
    fn name(&self) -> &'static str {
        "pitch"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.semitones = value,
            1 => self.cents = value,
            _ => self.preserve = value.round() >= 1.0,
        }
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.steered.reset(rate);
        self.apply();
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        self.steered.process(block, |_| None);
    }
    fn latency(&self) -> usize {
        self.steered.shifter.latency()
    }
}

/// Moves the spectral envelope, not the pitch: by semitones, times a factor.
pub struct Formant {
    semitones: f32,
    factor: f32,
    steered: Steered,
}

impl Formant {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("semitones", -12.0, 12.0, 0.0, "st"),
        ParamSpec::range("factor", 0.5, 2.0, 1.0, "x"),
    ];

    pub fn new() -> Self {
        Formant {
            semitones: 0.0,
            factor: 1.0,
            steered: Steered::new(2),
        }
    }

    fn apply(&mut self) {
        self.steered
            .set_formant(self.factor * ratio(self.semitones), false);
    }
}

impl Effect for Formant {
    fn name(&self) -> &'static str {
        "formant"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.semitones = value,
            _ => self.factor = value,
        }
        self.apply();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.steered.reset(rate);
        self.apply();
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        self.steered.process(block, |_| None);
    }
    fn latency(&self) -> usize {
        self.steered.shifter.latency()
    }
}

/// The input with an octave down and an octave up layered in.
pub struct Octaver {
    levels: [Smoothed; 3],
    down: Shifter,
    up: Shifter,
    dry: Delay,
    input: Vec<f32>,
    low: Vec<f32>,
    high: Vec<f32>,
}

impl Octaver {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("down", 0.0, 1.0, 0.5, ""),
        ParamSpec::range("up", 0.0, 1.0, 0.0, ""),
        ParamSpec::range("dry", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        Octaver {
            levels: [Smoothed::new(0.5), Smoothed::new(0.0), Smoothed::new(1.0)],
            down: Shifter::new(2, 3),
            up: Shifter::new(2, 4),
            dry: Delay::new(),
            input: vec![0.0; CHUNK * 2],
            low: vec![0.0; CHUNK * 2],
            high: vec![0.0; CHUNK * 2],
        }
    }
}

impl Effect for Octaver {
    fn name(&self) -> &'static str {
        "octaver"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        self.levels[check(self.name(), Self::SPECS, name, value)?].set(value);
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        for l in &mut self.levels {
            l.reset(rate);
        }
        self.down.reset(rate);
        self.up.reset(rate);
        self.down.transpose(0.5);
        self.up.transpose(2.0);
        self.dry.reset(self.down.latency());
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.down.stretch.is_none() {
            return;
        }
        finite(block);
        for chunk in block.chunks_mut(CHUNK) {
            let n = chunk.len() * 2;
            self.input[..n].copy_from_slice(chunk.as_flattened());
            self.down.process(&self.input[..n], &mut self.low[..n]);
            self.up.process(&self.input[..n], &mut self.high[..n]);
            for (k, f) in chunk.iter_mut().enumerate() {
                let d = self.dry.tick(*f);
                let [down, up, dry] = self.levels.each_mut().map(|l| l.next());
                for c in 0..2 {
                    f[c] = dry * d[c] + down * self.low[2 * k + c] + up * self.high[2 * k + c];
                }
            }
        }
    }
    fn latency(&self) -> usize {
        self.down.latency()
    }
}

/// Mono stretchers fed the same mono input, each into its own output buffer. A voice is fed
/// while it sounds: from when it is switched on until its level has faded out after it is
/// switched off, so switching voices on or off never cuts the others.
struct Voices {
    shifters: Vec<Shifter>,
    fed: Vec<bool>,
    input: Vec<f32>,
    outputs: Vec<Vec<f32>>,
}

impl Voices {
    fn new(count: usize) -> Self {
        Voices {
            shifters: (0..count).map(|i| Shifter::new(1, 10 + i as u32)).collect(),
            fed: vec![false; count],
            input: vec![0.0; CHUNK],
            outputs: vec![vec![0.0; CHUNK]; count],
        }
    }

    fn reset(&mut self, rate: f32, active: usize) {
        for (i, s) in self.shifters.iter_mut().enumerate() {
            s.reset(rate);
            self.fed[i] = i < active;
        }
    }

    /// Feeds the first `active` voices; one that was silent starts from a clean stretcher.
    fn wake(&mut self, active: usize) {
        for (s, fed) in self.shifters.iter_mut().zip(&mut self.fed).take(active) {
            if !*fed {
                if let Some(s) = &mut s.stretch {
                    s.reset();
                }
                *fed = true;
            }
        }
    }

    /// Stops feeding voice `i` from `active` on once `silent` says its level has reached 0.
    fn rest(&mut self, active: usize, silent: impl Fn(usize) -> bool) {
        for (i, fed) in self.fed.iter_mut().enumerate().skip(active) {
            if *fed && silent(i) {
                *fed = false;
            }
        }
    }

    fn ready(&self) -> bool {
        self.shifters[0].stretch.is_some()
    }

    fn latency(&self) -> usize {
        self.shifters[0].latency()
    }

    /// The voices being fed, over the first `n` frames of `input`.
    fn process(&mut self, n: usize) {
        for ((s, out), _) in self
            .shifters
            .iter_mut()
            .zip(&mut self.outputs)
            .zip(&self.fed)
            .filter(|(_, fed)| **fed)
        {
            s.process(&self.input[..n], &mut out[..n]);
        }
    }
}

/// Up to four voices a number of scale degrees from the sung note, in a key.
pub struct Harmonizer {
    values: [f32; 8],
    tracker: Tracker,
    voices: Voices,
    dry: Delay,
    rate: f32,
    gains: [Smoothed; 4],
    mix: Smoothed,
}

impl Harmonizer {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::choice("key", KEYS),
        ParamSpec::choice("scale", SCALES),
        ParamSpec::range("voices", 1.0, 4.0, 2.0, "voices"),
        ParamSpec::range("interval1", -14.0, 14.0, 2.0, "degrees"),
        ParamSpec::range("interval2", -14.0, 14.0, 4.0, "degrees"),
        ParamSpec::range("interval3", -14.0, 14.0, -3.0, "degrees"),
        ParamSpec::range("interval4", -14.0, 14.0, 7.0, "degrees"),
        ParamSpec::range("mix", 0.0, 1.0, 0.5, ""),
    ];

    pub fn new() -> Self {
        let mut values = [0.0; 8];
        for (v, s) in values.iter_mut().zip(Self::SPECS) {
            *v = s.default;
        }
        let mut h = Harmonizer {
            values,
            tracker: Tracker::new(),
            voices: Voices::new(4),
            dry: Delay::new(),
            rate: 0.0,
            gains: [Smoothed::new(0.0); 4],
            mix: Smoothed::new(values[7]),
        };
        h.level();
        h
    }

    fn active(&self) -> usize {
        self.values[2].round() as usize
    }

    /// The voices' levels: their sum keeps the power of one voice, as unrelated pitches add.
    fn level(&mut self) {
        let active = self.active();
        let each = 1.0 / (active as f32).sqrt();
        for (i, g) in self.gains.iter_mut().enumerate() {
            g.set(if i < active { each } else { 0.0 });
        }
    }

    fn retarget(&mut self) {
        let Some(f) = self.tracker.freq() else { return };
        let scale = Scale::new(
            self.values[0].round() as usize,
            self.values[1].round() as usize,
        );
        let note = scale.nearest(midi(f, 0.0));
        for (v, s) in self.voices.shifters.iter_mut().enumerate() {
            let degrees = self.values[3 + v].round() as i32;
            s.transpose(ratio(scale.interval(note, degrees) as f32));
            s.formant(1.0, true, f / self.rate);
        }
    }
}

impl Effect for Harmonizer {
    fn name(&self) -> &'static str {
        "harmonizer"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        self.values[i] = value;
        match i {
            2 => {
                self.level();
                self.voices.wake(self.active());
            }
            7 => self.mix.set(value),
            _ => {}
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.voices.reset(rate, self.active());
        for s in &mut self.voices.shifters {
            s.transpose(1.0);
            s.formant(1.0, true, 0.0);
        }
        for g in self.gains.iter_mut().chain([&mut self.mix]) {
            g.reset(rate);
        }
        self.tracker
            .reset(rate, self.voices.shifters[0].input_latency());
        self.dry.reset(self.voices.latency());
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.voices.ready() {
            return;
        }
        finite(block);
        let mut start = 0;
        while start < block.len() {
            let n = next_chunk(block.len() - start, &self.tracker);
            let chunk = &mut block[start..start + n];
            for (k, f) in chunk.iter().enumerate() {
                self.voices.input[k] = mono(*f);
                if self.tracker.push(self.voices.input[k]) {
                    self.retarget();
                }
            }
            self.voices.process(n);
            for (k, f) in chunk.iter_mut().enumerate() {
                let d = self.dry.tick(*f);
                let mut wet = 0.0;
                for ((g, out), fed) in self
                    .gains
                    .iter_mut()
                    .zip(&self.voices.outputs)
                    .zip(&self.voices.fed)
                {
                    let g = g.next();
                    if *fed {
                        wet += g * out[k];
                    }
                }
                let mix = self.mix.next();
                *f = [
                    (1.0 - mix) * d[0] + mix * wet,
                    (1.0 - mix) * d[1] + mix * wet,
                ];
            }
            let gains = &self.gains;
            self.voices.rest(self.active(), |i| {
                gains[i].settled() && gains[i].value() == 0.0
            });
            start += n;
        }
    }
    fn latency(&self) -> usize {
        self.voices.latency()
    }
}

const MAX_UNISON: usize = 16;

/// Copies of the voice detuned across a spread of cents and panned across the stereo field.
pub struct Unison {
    values: [f32; 4],
    gains: [[Smoothed; 2]; MAX_UNISON],
    mix: Smoothed,
    voices: Voices,
    dry: Delay,
}

impl Unison {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("voices", 2.0, MAX_UNISON as f32, 4.0, "voices"),
        ParamSpec::range("detune", 0.0, 100.0, 15.0, "cents"),
        ParamSpec::range("spread", 0.0, 1.0, 1.0, ""),
        ParamSpec::range("mix", 0.0, 1.0, 0.5, ""),
    ];

    pub fn new() -> Self {
        let mut u = Unison {
            values: [4.0, 15.0, 1.0, 0.5],
            gains: [[Smoothed::new(0.0); 2]; MAX_UNISON],
            mix: Smoothed::new(0.5),
            voices: Voices::new(MAX_UNISON),
            dry: Delay::new(),
        };
        u.apply();
        u
    }

    fn active(&self) -> usize {
        self.values[0].round() as usize
    }

    fn apply(&mut self) {
        let n = self.active();
        let [_, detune, spread, mix] = self.values;
        self.mix.set(mix);
        let norm = (2.0 / n as f32).sqrt();
        for (i, gains) in self.gains.iter_mut().enumerate() {
            if i >= n {
                gains.iter_mut().for_each(|g| g.set(0.0));
                continue;
            }
            // -1 for the first voice, +1 for the last
            let place = 2.0 * i as f32 / (n - 1) as f32 - 1.0;
            self.voices.shifters[i].transpose(ratio(detune * place / 100.0));
            let angle = (spread * place + 1.0) * FRAC_PI_4;
            gains[0].set(angle.cos() * norm);
            gains[1].set(angle.sin() * norm);
        }
    }
}

impl Effect for Unison {
    fn name(&self) -> &'static str {
        "unison"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        self.values[i] = value;
        self.apply();
        self.voices.wake(self.active());
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.voices.reset(rate, self.active());
        self.apply();
        for g in self
            .gains
            .as_flattened_mut()
            .iter_mut()
            .chain([&mut self.mix])
        {
            g.reset(rate);
        }
        self.dry.reset(self.voices.latency());
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.voices.ready() {
            return;
        }
        finite(block);
        for chunk in block.chunks_mut(CHUNK) {
            for (k, f) in chunk.iter().enumerate() {
                self.voices.input[k] = mono(*f);
            }
            self.voices.process(chunk.len());
            for (k, f) in chunk.iter_mut().enumerate() {
                let d = self.dry.tick(*f);
                let mut wet = [0.0f32; 2];
                for ((g, out), fed) in self
                    .gains
                    .iter_mut()
                    .zip(&self.voices.outputs)
                    .zip(&self.voices.fed)
                {
                    let g = [g[0].next(), g[1].next()];
                    if *fed {
                        wet[0] += g[0] * out[k];
                        wet[1] += g[1] * out[k];
                    }
                }
                let mix = self.mix.next();
                *f = [
                    (1.0 - mix) * d[0] + mix * wet[0],
                    (1.0 - mix) * d[1] + mix * wet[1],
                ];
            }
            let gains = &self.gains;
            self.voices.rest(self.active(), |i| {
                gains[i].iter().all(|g| g.settled() && g.value() == 0.0)
            });
        }
    }
    fn latency(&self) -> usize {
        self.voices.latency()
    }
}

const VIBRATO_SLOWEST: f32 = 1.0;
const VIBRATO_DEEPEST: f32 = 200.0;

/// Periodic pitch modulation through a swinging delay: the pitch follows the delay's slope.
pub struct Vibrato {
    rate_hz: f32,
    depth: f32,
    rate: f32,
    buf: Vec<[f32; 2]>,
    write: usize,
    centre: f32,
    phase: f32,
    swing: f32,
}

impl Vibrato {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("rate", VIBRATO_SLOWEST, 12.0, 5.5, "Hz"),
        ParamSpec::range("depth", 0.0, VIBRATO_DEEPEST, 40.0, "cents"),
    ];

    pub fn new() -> Self {
        Vibrato {
            rate_hz: 5.5,
            depth: 40.0,
            rate: 0.0,
            buf: Vec::new(),
            write: 0,
            centre: 0.0,
            phase: 0.0,
            swing: 0.0,
        }
    }

    /// Delay swing in samples: a cosine of amplitude A at ω rad/sample bends the pitch by
    /// a factor 1 ± Aω, and 1 + depth·ln2/1200 is that many cents to first order.
    fn swing_for(&self, rate_hz: f32, depth: f32) -> f32 {
        depth * LN_2 / 1200.0 / (2.0 * PI * rate_hz / self.rate)
    }
}

impl Effect for Vibrato {
    fn name(&self) -> &'static str {
        "vibrato"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.rate_hz = value,
            _ => self.depth = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        let widest = self.swing_for(VIBRATO_SLOWEST, VIBRATO_DEEPEST);
        self.centre = widest.ceil() + 2.0;
        let len = ((self.centre + widest) as usize + 4).next_power_of_two();
        self.buf.clear();
        self.buf.resize(len, [0.0; 2]);
        self.write = 0;
        self.phase = 0.0;
        self.swing = self.swing_for(self.rate_hz, self.depth);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.buf.is_empty() {
            return;
        }
        finite(block);
        let mask = self.buf.len() - 1;
        let step = 2.0 * PI * self.rate_hz / self.rate;
        let target = self.swing_for(self.rate_hz, self.depth);
        // about 10 ms to follow a change, so a new depth does not jump the delay
        let follow = 1.0 - (-1.0 / (0.01 * self.rate)).exp();
        for f in block {
            self.buf[self.write] = *f;
            self.swing += (target - self.swing) * follow;
            let delay = (self.centre + self.swing * self.phase.cos()) as f64;
            let pos = (self.write + self.buf.len()) as f64 - delay;
            let base = pos.floor();
            let t = (pos - base) as f32;
            let i = base as usize;
            let at = |k: usize| self.buf[k & mask];
            let (ym, y0, y1, y2) = (at(i - 1), at(i), at(i + 1), at(i + 2));
            for c in 0..2 {
                let c1 = 0.5 * (y1[c] - ym[c]);
                let c2 = ym[c] - 2.5 * y0[c] + 2.0 * y1[c] - 0.5 * y2[c];
                let c3 = 0.5 * (y2[c] - ym[c]) + 1.5 * (y0[c] - y1[c]);
                f[c] = ((c3 * t + c2) * t + c1) * t + y0[c];
            }
            self.write = (self.write + 1) & mask;
            self.phase += step;
            if self.phase >= 2.0 * PI {
                self.phase -= 2.0 * PI;
            }
        }
    }
    fn latency(&self) -> usize {
        self.centre as usize
    }
}

/// splitmix64: a seeded, portable sequence.
struct Random(u64);

impl Random {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform in [-1, 1].
    fn signed(&mut self) -> f32 {
        (self.next() >> 40) as f32 / (1u64 << 23) as f32 - 1.0
    }
}

/// A slow random pitch wander: random targets `1/rate` seconds apart, eased between.
pub struct Drift {
    depth: f32,
    speed: f32,
    seed: u64,
    rate: f32,
    random: Random,
    from: f32,
    to: f32,
    along: f32,
    since: usize,
    shifter: Shifter,
    scratch: Vec<f32>,
}

/// Frames between drift's transpose updates, counted from reset, so where the host's blocks
/// begin and end does not change the sound.
const DRIFT_GRID: usize = CHUNK;

impl Drift {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("depth", 0.0, 100.0, 20.0, "cents"),
        ParamSpec::range("rate", 0.05, 2.0, 0.3, "Hz"),
        ParamSpec::range("seed", 0.0, 65535.0, 0.0, ""),
    ];

    pub fn new() -> Self {
        Drift {
            depth: 20.0,
            speed: 0.3,
            seed: 0,
            rate: 0.0,
            random: Random(0),
            from: 0.0,
            to: 0.0,
            along: 0.0,
            since: 0,
            shifter: Shifter::new(2, 5),
            scratch: vec![0.0; CHUNK * 2],
        }
    }
}

impl Effect for Drift {
    fn name(&self) -> &'static str {
        "drift"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.depth = value,
            1 => self.speed = value,
            _ => self.seed = value.round() as u64,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.shifter.reset(rate);
        self.random = Random(self.seed);
        self.from = 0.0;
        self.to = self.random.signed();
        self.along = 0.0;
        self.since = 0;
        self.shifter.transpose(1.0);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.shifter.stretch.is_none() {
            return;
        }
        finite(block);
        let mut start = 0;
        while start < block.len() {
            if self.since == 0 {
                let t = self.along;
                let eased = t * t * (3.0 - 2.0 * t);
                let wander = self.from + (self.to - self.from) * eased;
                self.shifter.transpose(ratio(self.depth * wander / 100.0));
            }
            let n = (block.len() - start).min(DRIFT_GRID - self.since);
            self.shifter
                .process_stereo(&mut self.scratch, &mut block[start..start + n]);
            start += n;
            self.since += n;
            if self.since == DRIFT_GRID {
                self.since = 0;
                self.along += DRIFT_GRID as f32 * self.speed / self.rate;
                while self.along >= 1.0 {
                    self.along -= 1.0;
                    self.from = self.to;
                    self.to = self.random.signed();
                }
            }
        }
    }
    fn latency(&self) -> usize {
        self.shifter.latency()
    }
}

/// A stereo shifter steered by a pitch tracker, mixed with the dry. The detected pitch also
/// tells the formant analysis where the fundamental is: left to itself it can miss.
struct Steered {
    tracker: Tracker,
    shifter: Shifter,
    dry: Delay,
    scratch: Vec<f32>,
    rate: f32,
    formant: f32,
    compensate: bool,
    base: f32,
    mix: Smoothed,
}

impl Steered {
    fn new(seed: u32) -> Self {
        Steered {
            tracker: Tracker::new(),
            shifter: Shifter::new(2, seed),
            dry: Delay::new(),
            scratch: vec![0.0; CHUNK * 2],
            rate: 0.0,
            formant: 1.0,
            compensate: true,
            base: 0.0,
            mix: Smoothed::new(1.0),
        }
    }

    fn set_formant(&mut self, factor: f32, compensate: bool) {
        self.formant = factor;
        self.compensate = compensate;
        self.shifter.formant(factor, compensate, self.base);
    }

    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.base = 0.0;
        self.shifter.reset(rate);
        self.shifter.transpose(1.0);
        self.set_formant(self.formant, self.compensate);
        self.tracker.reset(rate, self.shifter.input_latency());
        self.dry.reset(self.shifter.latency());
        self.mix.reset(rate);
    }

    /// `steer` gets each new detection (Hz, None when unvoiced) and returns the transpose
    /// factor to use from then on, or None to keep the current one.
    fn process(
        &mut self,
        block: &mut [[f32; 2]],
        mut steer: impl FnMut(Option<f32>) -> Option<f32>,
    ) {
        if self.shifter.stretch.is_none() {
            return;
        }
        finite(block);
        let mut start = 0;
        while start < block.len() {
            let n = next_chunk(block.len() - start, &self.tracker);
            let chunk = &mut block[start..start + n];
            for f in chunk.iter() {
                if self.tracker.push(mono(*f)) {
                    let detected = self.tracker.freq();
                    if let Some(factor) = steer(detected) {
                        self.shifter.transpose(factor);
                    }
                    if let Some(f0) = detected {
                        self.base = f0 / self.rate;
                        self.shifter
                            .formant(self.formant, self.compensate, self.base);
                    }
                }
            }
            self.shifter.process_stereo(&mut self.scratch, chunk);
            for (k, f) in chunk.iter_mut().enumerate() {
                let d = self
                    .dry
                    .tick([self.scratch[2 * k], self.scratch[2 * k + 1]]);
                let mix = self.mix.next();
                *f = [
                    mix * f[0] + (1.0 - mix) * d[0],
                    mix * f[1] + (1.0 - mix) * d[1],
                ];
            }
            start += n;
        }
    }
}

/// A monotone: every detected pitch moved onto one note.
pub struct Robot {
    note: f32,
    steered: Steered,
}

impl Robot {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("note", 36.0, 84.0, 57.0, "midi"),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        Robot {
            note: 57.0,
            steered: Steered::new(6),
        }
    }
}

impl Effect for Robot {
    fn name(&self) -> &'static str {
        "robot"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.note = value.round(),
            _ => self.steered.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.steered.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        let target = freq(self.note, 0.0);
        self.steered
            .process(block, |f| f.map(|f| (target / f).clamp(0.25, 4.0)));
    }
    fn latency(&self) -> usize {
        self.steered.shifter.latency()
    }
}

/// How much nearer another note must be before tune leaves the one it holds, in semitones.
const HYSTERESIS: f32 = 0.1;
/// The slowest extra retune time humanize gives a sustained note.
const HUMANIZE: f32 = 0.3;
/// Unvoiced for this long, in seconds, and tune lets its correction go. Shorter gaps are a
/// consonant or a missed detection inside a phrase, where dropping the correction would make
/// the note wobble; a breath between phrases is several times longer.
const RELEASE_AFTER: f32 = 0.06;
/// The time constant the released correction falls to nothing with, in seconds: gone well
/// within a breath, so the next note starts from its own pitch.
const RELEASE: f32 = 0.05;

/// Pitch correction to the nearest note of a key and scale.
pub struct Tune {
    values: [f32; 6],
    steered: Steered,
    target: Option<i32>,
    held: f32,
    unvoiced: f32,
    correction: f32,
}

impl Tune {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::choice("key", KEYS),
        ParamSpec::choice("scale", SCALES),
        ParamSpec::range("reference", -100.0, 100.0, 0.0, "cents"),
        ParamSpec::range("speed", 0.0, 400.0, 20.0, "ms"),
        ParamSpec::range("humanize", 0.0, 1.0, 0.0, ""),
        ParamSpec::range("mix", 0.0, 1.0, 1.0, ""),
    ];

    pub fn new() -> Self {
        Tune {
            values: [0.0, 0.0, 0.0, 20.0, 0.0, 1.0],
            steered: Steered::new(7),
            target: None,
            held: 0.0,
            unvoiced: 0.0,
            correction: 0.0,
        }
    }
}

impl Effect for Tune {
    fn name(&self) -> &'static str {
        "tune"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name(), Self::SPECS, name, value)?;
        self.values[i] = value;
        if i == 5 {
            self.steered.mix.set(value);
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.steered.reset(rate);
        self.target = None;
        self.held = 0.0;
        self.unvoiced = 0.0;
        self.correction = 0.0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        let [key, scale, reference, speed, humanize, _] = self.values;
        let scale = Scale::new(key.round() as usize, scale.round() as usize);
        let hop = self.steered.tracker.hop() as f32 / self.steered.rate;
        let (target, held, unvoiced, correction) = (
            &mut self.target,
            &mut self.held,
            &mut self.unvoiced,
            &mut self.correction,
        );
        self.steered.process(block, |detected| {
            let Some(f) = detected else {
                *target = None;
                *held = 0.0;
                *unvoiced += hop;
                if *unvoiced <= RELEASE_AFTER || *correction == 0.0 {
                    return None;
                }
                *correction *= (-hop / RELEASE).exp();
                if correction.abs() < 1e-3 {
                    *correction = 0.0;
                }
                return Some(ratio(*correction));
            };
            *unvoiced = 0.0;
            let sung = midi(f, reference);
            let nearest = scale.nearest(sung);
            match *target {
                Some(t)
                    if (sung - t as f32).abs() <= (sung - nearest as f32).abs() + HYSTERESIS =>
                {
                    *held += hop
                }
                _ => {
                    *target = Some(nearest);
                    *held = 0.0;
                }
            }
            let goal = target.map_or(0.0, |t| t as f32 - sung);
            // a note counts as sustained from 100 ms on, fully from 300 ms
            let sustain = ((*held - 0.1) / 0.2).clamp(0.0, 1.0);
            let tau = speed / 1000.0 + humanize * HUMANIZE * sustain;
            *correction = if tau <= 0.0 {
                goal
            } else {
                *correction + (goal - *correction) * (1.0 - (-hop / tau).exp())
            };
            Some(ratio(*correction))
        });
    }
    fn latency(&self) -> usize {
        self.steered.shifter.latency()
    }
}
