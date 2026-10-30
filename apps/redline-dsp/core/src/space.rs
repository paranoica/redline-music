//! The space effects of the voice lab: reverbs, delays and modulation.
//!
//! - room, plate, hall, shimmer: Faust's zita-rev1, Dattorro plate, jpverb, and zita-rev1 with
//!   a pitch-shifted feedback (core/faust/*.dsp). The effect adds a pre-delay before the
//!   reverb, a width on its output and a dry/wet mix.
//! - gated_reverb: the room with a gate on its output keyed by the dry input: open while the
//!   input is above `threshold`, held `hold` after it falls under, then closed over `release`.
//! - reverse_reverb (offline only): the reverb of the reversed input, reversed back, so a swell
//!   rises into each onset and ends `predelay` before it. Run as a reverb backwards in time
//!   over overlapping windows: a window's output needs the input up to its longest tail after
//!   it, which is the effect's latency.
//! - delay: mono (both sides), ping-pong (left first, each repeat on the other side) or
//!   slapback (one repeat). The time in ms, or a note value at `bpm`; `tone` low-passes every
//!   repeat.
//! - tape_delay: a mono delay whose time wanders slowly (wow, seeded) and quickly (flutter),
//!   with tanh saturation in its loop.
//! - throws: ping-pong echoes of the last `tail` of each phrase only. A phrase ends where the
//!   input stays under `threshold` for `gap`; that is known `gap` late, so the effect runs
//!   [`Throws::GAP_MAX`] late and the echoes land as if it had known at once.
//! - chorus, flanger, phaser, doubler, autopan: modulated delays, an allpass chain and a pan,
//!   from LFOs that start at reset, so a render is the same every time.

use crate::effect::{check, Effect, ParamError, ParamSpec, Smoothed, MIN_RATE};
use crate::faust::{self, Dsp, ParamIndex};
use crate::voice::Random;
use std::f32::consts::{FRAC_PI_2, PI};

const CHUNK: usize = 256;

fn frames(ms: f32, rate: f32) -> usize {
    (ms * rate / 1000.0).round().max(0.0) as usize
}

fn amplitude(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

fn clean(v: f32) -> f32 {
    if v.is_finite() {
        v
    } else {
        0.0
    }
}

fn usable(rate: f32) -> bool {
    rate >= MIN_RATE && rate.is_finite()
}

/// Note values a time can follow, and their length in beats.
const NOTES: &[&str] = &[
    "off",
    "1/1",
    "1/2",
    "1/4",
    "1/8",
    "1/8 dotted",
    "1/16",
    "1/4 triplet",
    "1/8 triplet",
    "1/16 triplet",
];
const BEATS: [f32; 10] = [
    0.0,
    4.0,
    2.0,
    1.0,
    0.5,
    0.75,
    0.25,
    2.0 / 3.0,
    1.0 / 3.0,
    1.0 / 6.0,
];
const BPM: ParamSpec = ParamSpec::range("bpm", 60.0, 200.0, 90.0, "bpm");
/// The longest note at the slowest tempo: a whole note at 60 bpm.
const NOTE_MAX_MS: f32 = 4000.0;

/// The time in ms: `ms` when not synced, else the note's beats at `bpm`.
fn synced_ms(ms: f32, note: usize, bpm: f32) -> f32 {
    if note == 0 {
        ms
    } else {
        60_000.0 / bpm * BEATS[note]
    }
}

/// `x` faded towards `wet` by `mix`.
fn blend(x: [f32; 2], wet: [f32; 2], mix: f32) -> [f32; 2] {
    [x[0] + (wet[0] - x[0]) * mix, x[1] + (wet[1] - x[1]) * mix]
}

/// The side of a stereo frame scaled by `width`.
fn widen(f: [f32; 2], width: f32) -> [f32; 2] {
    let mid = 0.5 * (f[0] + f[1]);
    let side = 0.5 * (f[0] - f[1]) * width;
    [mid + side, mid - side]
}

/// One channel's past, read a whole or fractional number of frames back.
struct Line {
    buf: Vec<f32>,
    pos: usize,
}

impl Line {
    fn new() -> Self {
        Line {
            buf: Vec::new(),
            pos: 0,
        }
    }

    fn reset(&mut self, frames: usize) {
        let len = (frames + 4).next_power_of_two();
        self.buf.clear();
        self.buf.resize(len, 0.0);
        self.pos = 0;
    }

    fn ready(&self) -> bool {
        !self.buf.is_empty()
    }

    fn write(&mut self, v: f32) {
        self.buf[self.pos] = v;
        self.pos = (self.pos + 1) & (self.buf.len() - 1);
    }

    /// The frame written `delay` writes ago, 1 being the last.
    fn back(&self, delay: usize) -> f32 {
        self.buf[self.pos.wrapping_sub(delay) & (self.buf.len() - 1)]
    }

    /// Between frames, linearly; never closer than the last frame written.
    fn read(&self, delay: f32) -> f32 {
        let d = delay.max(1.0);
        let k = d.floor();
        let t = d - k;
        let k = k as usize;
        let a = self.back(k);
        if t == 0.0 {
            a
        } else {
            a + (self.back(k + 1) - a) * t
        }
    }
}

/// A one-pole low pass with a gain of 1 at 0 Hz.
#[derive(Clone, Copy, Default)]
struct OnePole {
    y: f32,
}

impl OnePole {
    fn coefficient(freq: f32, rate: f32) -> f32 {
        (1.0 - (-2.0 * PI * freq / rate).exp()).clamp(0.0, 1.0)
    }

    fn tick(&mut self, x: f32, a: f32) -> f32 {
        self.y += a * (x - self.y);
        self.y
    }
}

/// An LFO's phase in cycles, counted from reset.
#[derive(Clone, Copy, Default)]
struct Phase(f64);

impl Phase {
    fn advance(&mut self, hz: f32, rate: f32) -> f32 {
        let p = self.0 as f32;
        self.0 = (self.0 + hz as f64 / rate as f64).fract();
        p
    }
}

fn sine(cycles: f32) -> f32 {
    (2.0 * PI * cycles).sin()
}

// --- reverbs ---

/// A generated DSP on the heap, fed and drained in chunks of interleaved frames.
struct Engine<D: Dsp> {
    dsp: Option<Box<D>>,
    rate: f32,
    values: [(ParamIndex, f32); 4],
    count: usize,
    input: [[f32; CHUNK]; 2],
    output: [[f32; CHUNK]; 2],
}

impl<D: Dsp> Engine<D> {
    fn new() -> Self {
        Engine {
            dsp: None,
            rate: 0.0,
            values: [(ParamIndex(0), 0.0); 4],
            count: 0,
            input: [[0.0; CHUNK]; 2],
            output: [[0.0; CHUNK]; 2],
        }
    }

    /// A slider kept by its label, so a reset can give it back to the DSP.
    fn declare(&mut self, label: &str, value: f32) -> usize {
        self.values[self.count] = (faust::param::<D>(label), value);
        self.count += 1;
        self.count - 1
    }

    fn set(&mut self, slot: usize, value: f32) {
        self.values[slot].1 = value;
        if let Some(d) = &mut self.dsp {
            d.set_param(self.values[slot].0, value);
        }
    }

    /// Ready at `rate`, or switched off under the rack's lowest rate.
    fn reset(&mut self, rate: f32) {
        if !usable(rate) {
            self.dsp = None;
            self.rate = 0.0;
            return;
        }
        let dsp = self.dsp.get_or_insert_with(faust::boxed::<D>);
        if self.rate == rate {
            dsp.clear();
        } else {
            dsp.init(rate.round() as i32);
            self.rate = rate;
        }
        for &(p, v) in &self.values[..self.count] {
            dsp.set_param(p, v);
        }
    }

    fn clear(&mut self) {
        if let Some(d) = &mut self.dsp {
            d.clear();
        }
    }

    fn ready(&self) -> bool {
        self.dsp.is_some()
    }

    /// The first `n` frames of `input` through the DSP into `output`.
    fn compute(&mut self, n: usize) {
        if let Some(d) = &mut self.dsp {
            let [il, ir] = &self.input;
            let [ol, or] = &mut self.output;
            d.compute([&il[..n], &ir[..n]], [&mut ol[..n], &mut or[..n]]);
        }
    }
}

const PREDELAY_MAX_MS: f32 = 250.0;

const fn mix_spec(default: f32) -> ParamSpec {
    ParamSpec::range("mix", 0.0, 1.0, default, "")
}
const PREDELAY_SPEC: ParamSpec = ParamSpec::range("predelay", 0.0, PREDELAY_MAX_MS, 10.0, "ms");
const WIDTH_SPEC: ParamSpec = ParamSpec::range("width", 0.0, 2.0, 1.0, "x");

/// Where a reverb's param goes.
#[derive(Clone, Copy)]
enum Target {
    Mix,
    Predelay,
    Width,
    Slider(usize),
    Threshold,
    Hold,
    Release,
}

/// A gate keyed by the dry input: open above the threshold, held, then closed over a release.
struct Gate {
    threshold: f32,
    hold: f32,
    release: f32,
    since: usize,
    gain: f32,
    rate: f32,
}

impl Gate {
    fn new() -> Self {
        Gate {
            threshold: amplitude(-30.0),
            hold: 150.0,
            release: 50.0,
            since: usize::MAX,
            gain: 0.0,
            rate: 48000.0,
        }
    }

    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.since = usize::MAX;
        self.gain = 0.0;
    }

    fn tick(&mut self, key: f32) -> f32 {
        if key > self.threshold {
            self.since = 0;
        } else {
            self.since = self.since.saturating_add(1);
        }
        let hold = frames(self.hold, self.rate);
        let target = if self.since <= hold { 1.0 } else { 0.0 };
        // opening over a millisecond so a gate snapping open on a tail does not click
        let step = if target > self.gain {
            1.0 / frames(1.0, self.rate).max(1) as f32
        } else {
            1.0 / frames(self.release, self.rate).max(1) as f32
        };
        self.gain = if target > self.gain {
            (self.gain + step).min(1.0)
        } else {
            (self.gain - step).max(0.0)
        };
        self.gain
    }
}

/// A Faust reverb with a pre-delay, width and mix, and for gated_reverb a gate.
pub struct Reverb<D: Dsp> {
    name: &'static str,
    specs: &'static [ParamSpec],
    targets: [Target; 8],
    engine: Engine<D>,
    gate: Option<Gate>,
    predelay: [Line; 2],
    predelay_ms: f32,
    rate: f32,
    mix: Smoothed,
    width: Smoothed,
}

impl<D: Dsp> Reverb<D> {
    fn build(name: &'static str, specs: &'static [ParamSpec], gated: bool) -> Self {
        let mut engine = Engine::new();
        let mut targets = [Target::Mix; 8];
        for (t, s) in targets.iter_mut().zip(specs) {
            *t = match s.name {
                "mix" => Target::Mix,
                "predelay" => Target::Predelay,
                "width" => Target::Width,
                "threshold" => Target::Threshold,
                "hold" => Target::Hold,
                "release" => Target::Release,
                label => Target::Slider(engine.declare(label, s.default)),
            };
        }
        let mut r = Reverb {
            name,
            specs,
            targets,
            engine,
            gate: gated.then(Gate::new),
            predelay: [Line::new(), Line::new()],
            predelay_ms: 0.0,
            rate: 0.0,
            mix: Smoothed::new(0.0),
            width: Smoothed::new(1.0),
        };
        for s in specs {
            r.set(s.name, s.default).expect("defaults are in range");
        }
        r
    }
}

pub fn room() -> Reverb<faust::Room> {
    const SPECS: &[ParamSpec] = &[
        mix_spec(0.3),
        PREDELAY_SPEC,
        WIDTH_SPEC,
        ParamSpec::range("decay", 0.1, 10.0, 0.8, "s"),
        ParamSpec::range("damping", 500.0, 20000.0, 6000.0, "Hz"),
    ];
    Reverb::build("room", SPECS, false)
}

pub fn plate() -> Reverb<faust::Plate> {
    const SPECS: &[ParamSpec] = &[
        mix_spec(0.3),
        PREDELAY_SPEC,
        WIDTH_SPEC,
        ParamSpec::range("decay", 0.2, 10.0, 1.5, "s"),
        ParamSpec::range("damping", 0.0, 0.95, 0.3, ""),
    ];
    Reverb::build("plate", SPECS, false)
}

pub fn hall() -> Reverb<faust::Hall> {
    const SPECS: &[ParamSpec] = &[
        mix_spec(0.3),
        PREDELAY_SPEC,
        WIDTH_SPEC,
        ParamSpec::range("decay", 0.5, 20.0, 3.0, "s"),
        ParamSpec::range("size", 0.5, 5.0, 2.0, "x"),
        ParamSpec::range("damping", 0.0, 0.95, 0.3, ""),
    ];
    Reverb::build("hall", SPECS, false)
}

pub fn shimmer() -> Reverb<faust::Shimmer> {
    const SPECS: &[ParamSpec] = &[
        mix_spec(0.3),
        PREDELAY_SPEC,
        WIDTH_SPEC,
        ParamSpec::range("decay", 0.5, 20.0, 4.0, "s"),
        ParamSpec::range("damping", 500.0, 20000.0, 8000.0, "Hz"),
        ParamSpec::range("shift", -12.0, 12.0, 12.0, "st"),
        ParamSpec::range("feedback", 0.0, 0.9, 0.5, ""),
    ];
    Reverb::build("shimmer", SPECS, false)
}

pub fn gated_reverb() -> Reverb<faust::Room> {
    const SPECS: &[ParamSpec] = &[
        mix_spec(0.5),
        PREDELAY_SPEC,
        WIDTH_SPEC,
        ParamSpec::range("decay", 0.1, 10.0, 2.0, "s"),
        ParamSpec::range("damping", 500.0, 20000.0, 8000.0, "Hz"),
        ParamSpec::range("threshold", -60.0, 0.0, -30.0, "dB"),
        ParamSpec::range("hold", 10.0, 1000.0, 150.0, "ms"),
        ParamSpec::range("release", 1.0, 500.0, 50.0, "ms"),
    ];
    Reverb::build("gated_reverb", SPECS, true)
}

impl<D: Dsp> Effect for Reverb<D> {
    fn name(&self) -> &'static str {
        self.name
    }
    fn params(&self) -> &'static [ParamSpec] {
        self.specs
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        let i = check(self.name, self.specs, name, value)?;
        match self.targets[i] {
            Target::Mix => self.mix.set(value),
            Target::Predelay => self.predelay_ms = value,
            Target::Width => self.width.set(value),
            Target::Slider(slot) => self.engine.set(slot, value),
            Target::Threshold => {
                if let Some(g) = &mut self.gate {
                    g.threshold = amplitude(value);
                }
            }
            Target::Hold => {
                if let Some(g) = &mut self.gate {
                    g.hold = value;
                }
            }
            Target::Release => {
                if let Some(g) = &mut self.gate {
                    g.release = value;
                }
            }
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.engine.reset(rate);
        if !self.engine.ready() {
            return;
        }
        for l in &mut self.predelay {
            l.reset(frames(PREDELAY_MAX_MS, rate) + 1);
        }
        self.mix.reset(rate);
        self.width.reset(rate);
        if let Some(g) = &mut self.gate {
            g.reset(rate);
        }
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.engine.ready() {
            return;
        }
        let pre = frames(self.predelay_ms, self.rate);
        for chunk in block.chunks_mut(CHUNK) {
            for (i, f) in chunk.iter_mut().enumerate() {
                *f = f.map(clean);
                for ((line, input), v) in
                    self.predelay.iter_mut().zip(&mut self.engine.input).zip(*f)
                {
                    line.write(v);
                    // a pre-delay of 0 reads the frame just written
                    input[i] = line.back(pre + 1);
                }
            }
            self.engine.compute(chunk.len());
            for (i, f) in chunk.iter_mut().enumerate() {
                let mut wet = widen(
                    [self.engine.output[0][i], self.engine.output[1][i]],
                    self.width.next(),
                );
                if let Some(g) = &mut self.gate {
                    let gain = g.tick(f[0].abs().max(f[1].abs()));
                    wet = [wet[0] * gain, wet[1] * gain];
                }
                *f = blend(*f, wet, self.mix.next());
            }
        }
    }
}

/// The room's reverb of the reversed input, reversed back.
pub struct ReverseReverb {
    engine: Engine<faust::Room>,
    decay: usize,
    damping: usize,
    predelay_ms: f32,
    rate: f32,
    mix: Smoothed,
    width: Smoothed,
    history: Vec<[f32; 2]>,
    queue: Vec<[f32; 2]>,
    now: u64,
    pos: usize,
    tail: usize,
    window: usize,
}

impl ReverseReverb {
    const DECAY_MAX: f32 = 4.0;
    const PREDELAY_MAX_MS: f32 = 500.0;
    const SPECS: &'static [ParamSpec] = &[
        mix_spec(0.5),
        ParamSpec::range("predelay", 0.0, Self::PREDELAY_MAX_MS, 0.0, "ms"),
        WIDTH_SPEC,
        ParamSpec::range("decay", 0.1, Self::DECAY_MAX, 1.5, "s"),
        ParamSpec::range("damping", 500.0, 20000.0, 6000.0, "Hz"),
    ];

    pub fn new() -> Self {
        let mut engine = Engine::new();
        let decay = engine.declare("decay", 1.5);
        let damping = engine.declare("damping", 6000.0);
        ReverseReverb {
            engine,
            decay,
            damping,
            predelay_ms: 0.0,
            rate: 0.0,
            mix: Smoothed::new(0.5),
            width: Smoothed::new(1.0),
            history: Vec::new(),
            queue: Vec::new(),
            now: 0,
            pos: 0,
            tail: 0,
            window: 0,
        }
    }

    fn at(&self, t: i64) -> [f32; 2] {
        if t < 0 || t as u64 >= self.now {
            return [0.0; 2];
        }
        self.history[t as usize & (self.history.len() - 1)]
    }

    /// The wet output for the window that ends a tail before now: the reverb run backwards
    /// from now, from silence, over the tail and the window.
    fn render(&mut self) {
        self.engine.clear();
        let now = self.now as i64;
        let pre = frames(self.predelay_ms, self.rate) as i64;
        let span = self.tail + self.window;
        let mut m0 = 0;
        while m0 < span {
            let n = CHUNK.min(span - m0);
            for i in 0..n {
                let x = self.at(now - 1 - (m0 + i) as i64 + pre);
                self.engine.input[0][i] = x[0];
                self.engine.input[1][i] = x[1];
            }
            self.engine.compute(n);
            for i in 0..n {
                let m = m0 + i;
                if m >= self.tail {
                    self.queue[span - 1 - m] = [self.engine.output[0][i], self.engine.output[1][i]];
                }
            }
            m0 += n;
        }
    }
}

impl Effect for ReverseReverb {
    fn name(&self) -> &'static str {
        "reverse_reverb"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.mix.set(value),
            1 => self.predelay_ms = value,
            2 => self.width.set(value),
            3 => self.engine.set(self.decay, value),
            _ => self.engine.set(self.damping, value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.engine.reset(rate);
        if !self.engine.ready() {
            return;
        }
        // a tail long enough for the longest decay after the longest pre-delay, and windows
        // as long as the tail: each frame is run through the reverb twice
        self.tail = frames(1000.0 * Self::DECAY_MAX + Self::PREDELAY_MAX_MS, rate);
        self.window = self.tail;
        self.history.clear();
        self.history
            .resize((self.tail + self.window + 2).next_power_of_two(), [0.0; 2]);
        self.queue.clear();
        self.queue.resize(self.window, [0.0; 2]);
        self.now = 0;
        self.pos = 0;
        self.mix.reset(rate);
        self.width.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.engine.ready() {
            return;
        }
        let latency = (self.tail + self.window) as i64;
        let mask = self.history.len() - 1;
        for f in block {
            let dry = self.at(self.now as i64 - latency);
            let wet = widen(self.queue[self.pos], self.width.next());
            self.history[self.now as usize & mask] = f.map(clean);
            self.now += 1;
            *f = blend(dry, wet, self.mix.next());
            self.pos += 1;
            if self.pos == self.window {
                self.pos = 0;
                self.render();
            }
        }
    }
    fn latency(&self) -> usize {
        self.tail + self.window
    }
    fn offline_only(&self) -> bool {
        true
    }
    fn grid(&self) -> usize {
        self.window.max(1)
    }
}

// --- delays ---

/// Echoes in one of three modes, at a time in ms or a note value.
pub struct Delay {
    mode: usize,
    time: f32,
    note: usize,
    bpm: f32,
    feedback: f32,
    tone: f32,
    rate: f32,
    delay: Smoothed,
    mix: Smoothed,
    lines: [Line; 2],
    filters: [OnePole; 2],
}

const TIME_MAX_MS: f32 = 2000.0;

impl Delay {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::choice("mode", &["mono", "pingpong", "slapback"]),
        ParamSpec::range("time", 1.0, TIME_MAX_MS, 375.0, "ms"),
        ParamSpec::choice("sync", NOTES),
        BPM,
        ParamSpec::range("feedback", 0.0, 0.95, 0.35, ""),
        ParamSpec::range("tone", 500.0, 20000.0, 8000.0, "Hz"),
        mix_spec(0.3),
    ];

    pub fn new() -> Self {
        Delay {
            mode: 0,
            time: 375.0,
            note: 0,
            bpm: BPM.default,
            feedback: 0.35,
            tone: 8000.0,
            rate: 0.0,
            delay: Smoothed::new(0.0),
            mix: Smoothed::new(0.3),
            lines: [Line::new(), Line::new()],
            filters: [OnePole::default(); 2],
        }
    }

    fn retime(&mut self) {
        let ms = synced_ms(self.time, self.note, self.bpm);
        self.delay.set((ms * self.rate / 1000.0).max(1.0));
    }
}

impl Effect for Delay {
    fn name(&self) -> &'static str {
        "delay"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.mode = value.round() as usize,
            1 => self.time = value,
            2 => self.note = value.round() as usize,
            3 => self.bpm = value,
            4 => self.feedback = value,
            5 => self.tone = value,
            _ => self.mix.set(value),
        }
        self.retime();
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        for l in &mut self.lines {
            l.reset(frames(NOTE_MAX_MS.max(TIME_MAX_MS), rate) + 2);
        }
        self.filters = [OnePole::default(); 2];
        self.retime();
        self.delay.reset(rate);
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.lines[0].ready() {
            return;
        }
        let a = OnePole::coefficient(self.tone, self.rate);
        let fb = if self.mode == 2 { 0.0 } else { self.feedback };
        let [left, right] = &mut self.lines;
        let [lp0, lp1] = &mut self.filters;
        for f in block {
            *f = f.map(clean);
            let d = self.delay.next();
            let input = 0.5 * (f[0] + f[1]);
            let wet = if self.mode == 1 {
                let (l, r) = (left.read(d), right.read(d));
                left.write(lp0.tick(input + fb * r, a));
                right.write(lp1.tick(fb * l, a));
                [l, r]
            } else {
                let y = left.read(d);
                left.write(lp0.tick(input + fb * y, a));
                [y, y]
            };
            *f = blend(*f, wet, self.mix.next());
        }
    }
}

/// A mono delay on wandering tape, saturating in its loop.
pub struct TapeDelay {
    time: f32,
    feedback: f32,
    wow: f32,
    flutter: f32,
    drive: f32,
    tone: f32,
    seed: u64,
    rate: f32,
    mix: Smoothed,
    line: Line,
    filter: OnePole,
    random: Random,
    from: f32,
    to: f32,
    along: f32,
    flutter_phase: Phase,
}

impl TapeDelay {
    /// Wow: a new random offset this often, eased into; flutter: a sine this fast.
    const WOW_HZ: f32 = 0.7;
    const WOW_MS: f32 = 3.0;
    const FLUTTER_HZ: f32 = 6.5;
    const FLUTTER_MS: f32 = 0.3;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("time", 20.0, TIME_MAX_MS, 300.0, "ms"),
        ParamSpec::range("feedback", 0.0, 0.95, 0.45, ""),
        ParamSpec::range("wow", 0.0, 1.0, 0.3, ""),
        ParamSpec::range("flutter", 0.0, 1.0, 0.2, ""),
        ParamSpec::range("drive", 0.0, 24.0, 6.0, "dB"),
        ParamSpec::range("tone", 500.0, 20000.0, 5000.0, "Hz"),
        mix_spec(0.3),
        ParamSpec::range("seed", 0.0, 65535.0, 0.0, "").fixed(),
    ];

    pub fn new() -> Self {
        TapeDelay {
            time: 300.0,
            feedback: 0.45,
            wow: 0.3,
            flutter: 0.2,
            drive: amplitude(6.0),
            tone: 5000.0,
            seed: 0,
            rate: 0.0,
            mix: Smoothed::new(0.3),
            line: Line::new(),
            filter: OnePole::default(),
            random: Random(0),
            from: 0.0,
            to: 0.0,
            along: 0.0,
            flutter_phase: Phase::default(),
        }
    }
}

impl Effect for TapeDelay {
    fn name(&self) -> &'static str {
        "tape_delay"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.time = value,
            1 => self.feedback = value,
            2 => self.wow = value,
            3 => self.flutter = value,
            4 => self.drive = amplitude(value),
            5 => self.tone = value,
            6 => self.mix.set(value),
            _ => self.seed = value.round() as u64,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.line
            .reset(frames(TIME_MAX_MS + Self::WOW_MS + Self::FLUTTER_MS, rate) + 4);
        self.filter = OnePole::default();
        self.random = Random(self.seed);
        self.from = 0.0;
        self.to = self.random.signed();
        self.along = 0.0;
        self.flutter_phase = Phase::default();
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.line.ready() {
            return;
        }
        let rate = self.rate;
        let a = OnePole::coefficient(self.tone, rate);
        let base = self.time * rate / 1000.0;
        let (wow, flutter) = (
            self.wow * Self::WOW_MS * 0.001 * rate,
            self.flutter * Self::FLUTTER_MS * 0.001 * rate,
        );
        let step = Self::WOW_HZ / rate;
        for f in block {
            *f = f.map(clean);
            let eased = 0.5 - 0.5 * (PI * self.along).cos();
            let wander = self.from + (self.to - self.from) * eased;
            self.along += step;
            if self.along >= 1.0 {
                self.along -= 1.0;
                self.from = self.to;
                self.to = self.random.signed();
            }
            let shake = sine(self.flutter_phase.advance(Self::FLUTTER_HZ, rate));
            // offsets only add, so the head never reads ahead of the base time
            let d = base + wow * (1.0 + wander) + flutter * (1.0 + shake);
            let y = self.line.read(d);
            let input = 0.5 * (f[0] + f[1]) + self.feedback * y;
            let saturated = (self.drive * input).tanh() / self.drive;
            self.line.write(self.filter.tick(saturated, a));
            *f = blend(*f, [y, y], self.mix.next());
        }
    }
}

/// Ping-pong echoes of the end of each phrase.
pub struct Throws {
    time: f32,
    note: usize,
    bpm: f32,
    feedback: f32,
    tone: f32,
    level: Smoothed,
    threshold: f32,
    gap: f32,
    tail: f32,
    rate: f32,
    history: Vec<[f32; 2]>,
    send: Vec<f32>,
    lines: [Line; 2],
    filters: [OnePole; 2],
    level_env: f32,
    env_step: f32,
    now: u64,
    last: u64,
    phrase: bool,
    lag: usize,
}

impl Throws {
    /// The longest pause the effect waits for, and so its latency.
    pub const GAP_MAX: f32 = 500.0;
    const TAIL_MAX: f32 = 1000.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("time", 50.0, TIME_MAX_MS, 375.0, "ms"),
        ParamSpec::choice("sync", NOTES),
        BPM,
        ParamSpec::range("feedback", 0.0, 0.9, 0.4, ""),
        ParamSpec::range("tone", 500.0, 20000.0, 6000.0, "Hz"),
        ParamSpec::range("level", -24.0, 6.0, 0.0, "dB"),
        ParamSpec::range("threshold", -60.0, -10.0, -40.0, "dB"),
        ParamSpec::range("gap", 50.0, Self::GAP_MAX, 200.0, "ms"),
        ParamSpec::range("tail", 20.0, Self::TAIL_MAX, 300.0, "ms"),
    ];

    pub fn new() -> Self {
        Throws {
            time: 375.0,
            note: 0,
            bpm: BPM.default,
            feedback: 0.4,
            tone: 6000.0,
            level: Smoothed::new(1.0),
            threshold: amplitude(-40.0),
            gap: 200.0,
            tail: 300.0,
            rate: 0.0,
            history: Vec::new(),
            send: Vec::new(),
            lines: [Line::new(), Line::new()],
            filters: [OnePole::default(); 2],
            level_env: 0.0,
            env_step: 1.0,
            now: 0,
            last: 0,
            phrase: false,
            lag: 0,
        }
    }

    fn delay_frames(&self) -> usize {
        frames(synced_ms(self.time, self.note, self.bpm), self.rate).max(1)
    }

    /// The last `tail` before the phrase end `end`, never more than one echo time, put on
    /// the send where the echoes will read it.
    fn throw(&mut self, end: u64, delay: usize) {
        let tail = frames(self.tail, self.rate).min(delay) as u64;
        let (hmask, smask) = (self.history.len() - 1, self.send.len() - 1);
        for s in end.saturating_sub(tail)..end {
            let x = self.history[s as usize & hmask];
            self.send[s as usize & smask] = 0.5 * (x[0] + x[1]);
        }
    }
}

impl Effect for Throws {
    fn name(&self) -> &'static str {
        "throws"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.time = value,
            1 => self.note = value.round() as usize,
            2 => self.bpm = value,
            3 => self.feedback = value,
            4 => self.tone = value,
            5 => self.level.set(amplitude(value)),
            6 => self.threshold = amplitude(value),
            7 => self.gap = value,
            _ => self.tail = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.lag = frames(Self::GAP_MAX, rate);
        let longest = frames(NOTE_MAX_MS.max(TIME_MAX_MS), rate);
        self.history.clear();
        self.history.resize(
            (self.lag + frames(Self::TAIL_MAX, rate) + 2).next_power_of_two(),
            [0.0; 2],
        );
        self.send.clear();
        self.send
            .resize((longest + self.lag + 2).next_power_of_two(), 0.0);
        for l in &mut self.lines {
            l.reset(longest + 2);
        }
        self.filters = [OnePole::default(); 2];
        self.level_env = 0.0;
        self.env_step = 1.0 - (-1.0 / (0.005 * rate)).exp();
        self.now = 0;
        self.last = 0;
        self.phrase = false;
        self.level.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.history.is_empty() {
            return;
        }
        let delay = self.delay_frames();
        let gap = frames(self.gap, self.rate) as u64;
        let a = OnePole::coefficient(self.tone, self.rate);
        let (hmask, smask) = (self.history.len() - 1, self.send.len() - 1);
        let threshold = self.threshold * self.threshold;
        for f in block {
            let x = f.map(clean);
            let n = self.now;
            self.history[n as usize & hmask] = x;
            let mono = 0.5 * (x[0] + x[1]);
            self.level_env += self.env_step * (mono * mono - self.level_env);
            if self.level_env > threshold {
                self.phrase = true;
                self.last = n;
            } else if self.phrase && n - self.last >= gap {
                self.phrase = false;
                self.throw(self.last + 1, delay);
            }
            // the frame going out now came in `lag` frames ago
            let dry = if n >= self.lag as u64 {
                self.history[(n - self.lag as u64) as usize & hmask]
            } else {
                [0.0; 2]
            };
            let slot = n.wrapping_sub((self.lag + delay) as u64) as usize & smask;
            let sent = self.send[slot];
            self.send[slot] = 0.0;
            let [left, right] = &mut self.lines;
            let (back_l, back_r) = (left.back(delay), right.back(delay));
            let l = self.filters[0].tick(sent + self.feedback * back_r, a);
            let r = self.filters[1].tick(self.feedback * back_l, a);
            left.write(l);
            right.write(r);
            let g = self.level.next();
            *f = [dry[0] + g * l, dry[1] + g * r];
            self.now += 1;
        }
    }
    fn latency(&self) -> usize {
        self.lag
    }
}

// --- modulation ---

/// Copies of the input read through slowly swinging delays.
pub struct Chorus {
    voices: usize,
    speed: f32,
    depth: f32,
    rate: f32,
    mix: Smoothed,
    lines: [Line; 2],
    phase: Phase,
}

impl Chorus {
    const BASE_MS: f32 = 12.0;
    const DEPTH_MAX: f32 = 10.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("voices", 1.0, 6.0, 3.0, ""),
        ParamSpec::range("rate", 0.05, 5.0, 0.8, "Hz"),
        ParamSpec::range("depth", 0.0, Self::DEPTH_MAX, 3.0, "ms"),
        mix_spec(0.5),
    ];

    pub fn new() -> Self {
        Chorus {
            voices: 3,
            speed: 0.8,
            depth: 3.0,
            rate: 0.0,
            mix: Smoothed::new(0.5),
            lines: [Line::new(), Line::new()],
            phase: Phase::default(),
        }
    }
}

impl Effect for Chorus {
    fn name(&self) -> &'static str {
        "chorus"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.voices = value.round() as usize,
            1 => self.speed = value,
            2 => self.depth = value,
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        for l in &mut self.lines {
            l.reset(frames(Self::BASE_MS + 2.0 * Self::DEPTH_MAX, rate) + 4);
        }
        self.phase = Phase::default();
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.lines[0].ready() {
            return;
        }
        let ms = self.rate / 1000.0;
        let n = self.voices.max(1);
        for f in block {
            *f = f.map(clean);
            let p = self.phase.advance(self.speed, self.rate);
            let mut wet = [0.0; 2];
            for (c, w) in wet.iter_mut().enumerate() {
                self.lines[c].write(f[c]);
                for v in 0..n {
                    // the voices spread around the cycle, the right side a quarter behind
                    let at = p + v as f32 / n as f32 + 0.25 * c as f32;
                    let d = (Self::BASE_MS + self.depth * (1.0 + sine(at))) * ms;
                    *w += self.lines[c].read(d);
                }
                *w /= n as f32;
            }
            *f = blend(*f, wet, self.mix.next());
        }
    }
}

/// A short swept delay fed back into itself.
pub struct Flanger {
    speed: f32,
    depth: f32,
    feedback: f32,
    rate: f32,
    mix: Smoothed,
    lines: [Line; 2],
    phase: Phase,
}

impl Flanger {
    const MIN_MS: f32 = 0.3;
    const SWEEP_MS: f32 = 5.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("rate", 0.05, 10.0, 0.25, "Hz"),
        ParamSpec::range("depth", 0.0, 1.0, 0.8, ""),
        ParamSpec::range("feedback", -0.95, 0.95, 0.5, ""),
        mix_spec(0.5),
    ];

    pub fn new() -> Self {
        Flanger {
            speed: 0.25,
            depth: 0.8,
            feedback: 0.5,
            rate: 0.0,
            mix: Smoothed::new(0.5),
            lines: [Line::new(), Line::new()],
            phase: Phase::default(),
        }
    }
}

impl Effect for Flanger {
    fn name(&self) -> &'static str {
        "flanger"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.speed = value,
            1 => self.depth = value,
            2 => self.feedback = value,
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        for l in &mut self.lines {
            l.reset(frames(Self::MIN_MS + Self::SWEEP_MS, rate) + 4);
        }
        self.phase = Phase::default();
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.lines[0].ready() {
            return;
        }
        let ms = self.rate / 1000.0;
        for f in block {
            *f = f.map(clean);
            let p = self.phase.advance(self.speed, self.rate);
            let mut wet = [0.0; 2];
            for (c, w) in wet.iter_mut().enumerate() {
                let swing = 0.5 + 0.5 * sine(p + 0.25 * c as f32);
                let d = (Self::MIN_MS + self.depth * Self::SWEEP_MS * swing) * ms;
                let y = self.lines[c].read(d);
                self.lines[c].write(f[c] + self.feedback * y);
                *w = y;
            }
            *f = blend(*f, wet, self.mix.next());
        }
    }
}

/// First-order allpasses swept together, with feedback around them.
pub struct Phaser {
    stages: usize,
    speed: f32,
    depth: f32,
    feedback: f32,
    rate: f32,
    mix: Smoothed,
    state: [[f32; Self::STAGES_MAX]; 2],
    last: [f32; 2],
    phase: Phase,
}

impl Phaser {
    const STAGES_MAX: usize = 12;
    const CENTRE_HZ: f32 = 1000.0;
    /// Octaves either side of the centre at full depth.
    const OCTAVES: f32 = 2.5;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("stages", 2.0, 12.0, 4.0, ""),
        ParamSpec::range("rate", 0.05, 5.0, 0.5, "Hz"),
        ParamSpec::range("depth", 0.0, 1.0, 0.8, ""),
        ParamSpec::range("feedback", 0.0, 0.9, 0.5, ""),
        mix_spec(0.5),
    ];

    pub fn new() -> Self {
        Phaser {
            stages: 4,
            speed: 0.5,
            depth: 0.8,
            feedback: 0.5,
            rate: 0.0,
            mix: Smoothed::new(0.5),
            state: [[0.0; Self::STAGES_MAX]; 2],
            last: [0.0; 2],
            phase: Phase::default(),
        }
    }
}

impl Effect for Phaser {
    fn name(&self) -> &'static str {
        "phaser"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.stages = value.round() as usize,
            1 => self.speed = value,
            2 => self.depth = value,
            3 => self.feedback = value,
            _ => self.mix.set(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.state = [[0.0; Self::STAGES_MAX]; 2];
        self.last = [0.0; 2];
        self.phase = Phase::default();
        self.mix.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.rate <= 0.0 {
            return;
        }
        let stages = self.stages.min(Self::STAGES_MAX);
        for f in block {
            *f = f.map(clean);
            let p = self.phase.advance(self.speed, self.rate);
            let mut wet = [0.0; 2];
            for (c, w) in wet.iter_mut().enumerate() {
                let octaves = self.depth * Self::OCTAVES * sine(p + 0.25 * c as f32);
                let hz = (Self::CENTRE_HZ * octaves.exp2()).min(0.45 * self.rate);
                let t = (PI * hz / self.rate).tan();
                let k = (t - 1.0) / (t + 1.0);
                let mut x = f[c] + self.feedback * self.last[c];
                for z in &mut self.state[c][..stages] {
                    let y = k * x + *z;
                    *z = x - k * y;
                    x = y;
                }
                self.last[c] = x;
                *w = x;
            }
            *f = blend(*f, wet, self.mix.next());
        }
    }
}

/// Two copies of the voice on slowly wandering delays, one each side, under the dry voice.
pub struct Doubler {
    level: Smoothed,
    delay: f32,
    depth: f32,
    speed: f32,
    rate: f32,
    line: Line,
    phases: [Phase; 2],
}

impl Doubler {
    const DELAY_MAX: f32 = 60.0;
    const DEPTH_MAX: f32 = 5.0;
    /// The right copy's delay and wander rate against the left's, as in the vocal chain's
    /// double (18 and 27 ms, 0.31 and 0.43 Hz).
    const RIGHT_DELAY: f32 = 1.5;
    const RIGHT_RATE: f32 = 0.43 / 0.31;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("level", -24.0, 0.0, -4.0, "dB"),
        ParamSpec::range("delay", 5.0, Self::DELAY_MAX, 18.0, "ms"),
        ParamSpec::range("depth", 0.0, Self::DEPTH_MAX, 2.0, "ms"),
        ParamSpec::range("rate", 0.05, 2.0, 0.31, "Hz"),
    ];

    pub fn new() -> Self {
        Doubler {
            level: Smoothed::new(amplitude(-4.0)),
            delay: 18.0,
            depth: 2.0,
            speed: 0.31,
            rate: 0.0,
            line: Line::new(),
            phases: [Phase::default(); 2],
        }
    }
}

impl Effect for Doubler {
    fn name(&self) -> &'static str {
        "doubler"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.level.set(amplitude(value)),
            1 => self.delay = value,
            2 => self.depth = value,
            _ => self.speed = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.line
            .reset(frames(Self::RIGHT_DELAY * Self::DELAY_MAX + Self::DEPTH_MAX, rate) + 4);
        self.phases = [Phase::default(); 2];
        self.level.reset(rate);
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.line.ready() {
            return;
        }
        let ms = self.rate / 1000.0;
        for f in block {
            *f = f.map(clean);
            self.line.write(0.5 * (f[0] + f[1]));
            let g = self.level.next();
            for (c, (scale, speed)) in [(1.0, 1.0), (Self::RIGHT_DELAY, Self::RIGHT_RATE)]
                .into_iter()
                .enumerate()
            {
                let p = self.phases[c].advance(self.speed * speed, self.rate);
                // read back from the frame just written: a delay of d frames is d + 1 back
                let d = (self.delay * scale + self.depth * sine(p)) * ms + 1.0;
                f[c] += g * self.line.read(d);
            }
        }
    }
}

/// The sound moved between the sides by an LFO.
pub struct Autopan {
    speed: f32,
    note: usize,
    bpm: f32,
    depth: f32,
    shape: usize,
    rate: f32,
    phase: Phase,
    position: f32,
}

impl Autopan {
    /// The fastest the position moves, across the whole field per this many ms, so a square
    /// does not click.
    const SLEW_MS: f32 = 5.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("rate", 0.05, 20.0, 1.0, "Hz"),
        ParamSpec::choice("sync", NOTES),
        BPM,
        ParamSpec::range("depth", 0.0, 1.0, 1.0, ""),
        ParamSpec::choice("shape", &["sine", "triangle", "square"]),
    ];

    pub fn new() -> Self {
        Autopan {
            speed: 1.0,
            note: 0,
            bpm: BPM.default,
            depth: 1.0,
            shape: 0,
            rate: 0.0,
            phase: Phase::default(),
            position: 0.0,
        }
    }

    fn lfo(&self, p: f32) -> f32 {
        match self.shape {
            0 => sine(p),
            // up from 0 to 1 at a quarter, down to -1 at three quarters, like the sine
            1 => {
                let q = (p + 0.25).fract();
                1.0 - 4.0 * (q - 0.5).abs()
            }
            _ => {
                if p < 0.5 {
                    1.0
                } else {
                    -1.0
                }
            }
        }
    }
}

impl Effect for Autopan {
    fn name(&self) -> &'static str {
        "autopan"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.speed = value,
            1 => self.note = value.round() as usize,
            2 => self.bpm = value,
            3 => self.depth = value,
            _ => self.shape = value.round() as usize,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.phase = Phase::default();
        self.position = 0.0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.rate <= 0.0 {
            return;
        }
        let hz = if self.note == 0 {
            self.speed
        } else {
            1000.0 / synced_ms(0.0, self.note, self.bpm)
        };
        let slew = 2.0 / (Self::SLEW_MS * 0.001 * self.rate).max(1.0);
        for f in block {
            *f = f.map(clean);
            let p = self.phase.advance(hz, self.rate);
            let target = self.depth * self.lfo(p);
            self.position += (target - self.position).clamp(-slew, slew);
            // as pan does: the far side turned down along a quarter cosine
            let far = (self.position.abs() * FRAC_PI_2).cos();
            if self.position > 0.0 {
                f[0] *= far;
            } else {
                f[1] *= far;
            }
        }
    }
}
