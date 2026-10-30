//! The time effects of the voice lab. Every one gives back as many frames as it is given, so
//! effects that change speed cannot run through the whole input: they read through a buffer
//! sized in `reset` and jump, with a short crossfade, when they run out of it.
//!
//! - varispeed: speed and pitch together, like a record. The head reads behind the newest
//!   input. Faster than 1 it catches up and then jumps a window back, replaying the last
//!   window; slower than 1 it falls behind and, a window behind, jumps a window forward,
//!   skipping what it missed. At the very start there is less than a window of past, so the
//!   first loops replay what has come in so far. Speed-ups are not band limited.
//! - timestretch: the same head, through signalsmith-stretch, so the pitch stays. A whole
//!   stretched take trimmed or padded to the input's length would need the input's future
//!   (at 2.0 the second half of the take by the middle of the output), which no streaming
//!   effect has; the windowed head is what a stream can do.
//! - tape_stop: from `time` the speed falls from 1 to 0 over `duration` as (1 - u)^curve,
//!   then silence; `resume` seconds later the live input comes back at full speed, spliced
//!   in (0 keeps the silence for good). tape_start: silence until `time`, then the speed rises as
//!   u^curve from the input at `time`; once up to speed it splices back onto the live input,
//!   dropping the lag the spin-up built, so it stays in time with the beat.
//! - reverse: windows played backwards one window late (latency is one window). Fixed
//!   windows only; offline only because the lateness is the window.
//! - freeze: while `hold` is on, the last `length` of input before the hold loops, its end
//!   crossfaded into its start.
//! - granular: Hann grains read around a position that moves at `stretch` times real time,
//!   scattered back by up to `scatter` and detuned by up to `jitter`. The position never
//!   needs future input: faster than 1 it jumps back over the last two seconds like
//!   varispeed, so granular runs in real time. Seeded, so a seed always gives the same grains.
//! - stutter: on the trigger's rising edge, the last `length` of input repeats `repeats`
//!   times in its place, each repeat `fall` dB quieter and `step` semitones higher (a faster
//!   repeat ends early, a slower one is cut at the slice's length); then the input returns.
//! - chop: sixteenths at `bpm`, in cycles of `steps`; step j plays slice `sj` of its cycle,
//!   or of the cycle before when `sj` is later than j (there is nothing to play yet in the
//!   first cycle). Cuts get 3 ms fades. Patterns are the params s0..s15: "0 0 2 3 4 4 6 7"
//!   is steps 8, s0 0, s1 0, s2 2 and so on.
//! - trance_gate: 16 sixteenths at `bpm`, each on or off; off steps sit `depth` dB down,
//!   reached over `release` ms, and on steps come back over `attack` ms.
//!
//! The grids of chop and trance_gate count from reset; a change of bpm starts a new count.
//!
//! Presets are settings, not effects:
//! - nightcore: varispeed at speed 1.25, a window of 4000 ms;
//! - chopped & screwed: varispeed at speed 0.8, a window of 4000 ms, then chop at the
//!   song's bpm with steps 8 and s0..s7 = 0 0 2 3 4 4 6 7.

use crate::effect::{check, Effect, ParamError, ParamSpec, MIN_RATE};
use crate::stretch::Stretch;
use crate::voice::Random;
use std::f32::consts::PI;

const ON_OFF: &[&str] = &["off", "on"];
/// The crossfade over a jump of a read head.
const SPLICE_MS: f32 = 10.0;
const WINDOW_MAX_MS: f32 = 4000.0;
/// Frames per call into the stretcher.
const CHUNK: usize = 256;

fn frames(ms: f32, rate: f32) -> usize {
    (ms * 0.001 * rate).round() as usize
}

fn amplitude(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

fn ratio(semitones: f32) -> f32 {
    2f32.powf(semitones / 12.0)
}

fn on(value: f32) -> bool {
    value.round() >= 1.0
}

fn scale(f: [f32; 2], g: f32) -> [f32; 2] {
    [f[0] * g, f[1] * g]
}

/// `a` to `b` by `t`: exactly `a` at 0 and exactly `b` at 1.
fn mix(a: [f32; 2], b: [f32; 2], t: f32) -> [f32; 2] {
    [a[0] * (1.0 - t) + b[0] * t, a[1] * (1.0 - t) + b[1] * t]
}

/// The input's recent past; delay 0 is the newest frame.
struct History {
    buf: Vec<[f32; 2]>,
    newest: usize,
    written: u64,
}

impl History {
    fn new() -> Self {
        History {
            buf: Vec::new(),
            newest: 0,
            written: 0,
        }
    }

    fn reset(&mut self, frames: usize) {
        let len = (frames + 4).next_power_of_two();
        self.buf.clear();
        self.buf.resize(len, [0.0; 2]);
        self.newest = 0;
        self.written = 0;
    }

    fn ready(&self) -> bool {
        !self.buf.is_empty()
    }

    fn push(&mut self, f: [f32; 2]) {
        self.newest = (self.newest + 1) & (self.buf.len() - 1);
        self.buf[self.newest] = f;
        self.written += 1;
    }

    fn back(&self, delay: usize) -> [f32; 2] {
        self.buf[self.newest.wrapping_sub(delay) & (self.buf.len() - 1)]
    }

    /// Between frames by Catmull-Rom; a delay under 0 reads the newest frame.
    fn at(&self, delay: f64) -> [f32; 2] {
        let d = delay.max(0.0);
        let k = d.floor();
        let t = (d - k) as f32;
        let k = k as usize;
        if t == 0.0 {
            return self.back(k);
        }
        let (ym, y0, y1, y2) = (
            self.back(k.saturating_sub(1)),
            self.back(k),
            self.back(k + 1),
            self.back(k + 2),
        );
        let mut out = [0.0; 2];
        for c in 0..2 {
            let c1 = 0.5 * (y1[c] - ym[c]);
            let c2 = ym[c] - 2.5 * y0[c] + 2.0 * y1[c] - 0.5 * y2[c];
            let c3 = 0.5 * (y2[c] - ym[c]) + 1.5 * (y0[c] - y1[c]);
            out[c] = ((c3 * t + c2) * t + c1) * t + y0[c];
        }
        out
    }

    /// The delay of the oldest frame still held.
    fn oldest(&self) -> f64 {
        self.written.min(self.buf.len() as u64).saturating_sub(1) as f64
    }
}

/// A read head that jumps a window when it leaves `low..=low + window`, fading from where it
/// was over a splice.
struct Head {
    delay: f64,
    old: f64,
    fade: usize,
    splice: usize,
}

impl Head {
    fn new() -> Self {
        Head {
            delay: 0.0,
            old: 0.0,
            fade: 0,
            splice: 1,
        }
    }

    fn reset(&mut self, delay: f64, splice: usize) {
        *self = Head {
            delay,
            old: delay,
            fade: 0,
            splice: splice.max(1),
        };
    }

    /// Both heads `by` frames further behind; no jump back reaches past `oldest`.
    fn drift(&mut self, by: f64, low: f64, window: f64, oldest: f64) {
        self.delay += by;
        self.old += by;
        if self.delay < low {
            self.jump((self.delay + window).min(oldest));
        } else if self.delay > low + window {
            self.jump(self.delay - window);
        }
    }

    fn jump(&mut self, to: f64) {
        self.old = self.delay;
        self.delay = to;
        self.fade = self.splice;
    }

    /// The old head's share of the next frame.
    fn weight(&mut self) -> f32 {
        if self.fade == 0 {
            return 0.0;
        }
        let w = self.fade as f32 / (self.splice + 1) as f32;
        self.fade -= 1;
        w
    }
}

/// Speed and pitch together.
pub struct Varispeed {
    speed: f32,
    window: f32,
    rate: f32,
    history: History,
    head: Head,
}

impl Varispeed {
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("speed", 0.5, 2.0, 1.0, "x"),
        ParamSpec::range("window", 100.0, WINDOW_MAX_MS, 1000.0, "ms"),
    ];

    pub fn new() -> Self {
        Varispeed {
            speed: 1.0,
            window: 1000.0,
            rate: 0.0,
            history: History::new(),
            head: Head::new(),
        }
    }
}

impl Effect for Varispeed {
    fn name(&self) -> &'static str {
        "varispeed"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.speed = value,
            _ => self.window = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.history
            .reset(frames(WINDOW_MAX_MS + 2.0 * SPLICE_MS, rate) + 8);
        self.head.reset(0.0, frames(SPLICE_MS, rate));
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.history.ready() {
            return;
        }
        let window = frames(self.window, self.rate) as f64;
        let by = 1.0 - self.speed as f64;
        for f in block {
            self.history.push(*f);
            self.head.drift(by, 0.0, window, self.history.oldest());
            let w = self.head.weight();
            let y = self.history.at(self.head.delay);
            *f = if w > 0.0 {
                mix(y, self.history.at(self.head.old), w)
            } else {
                y
            };
        }
    }
}

/// Speed without pitch: the varispeed head feeding signalsmith-stretch, which stretches
/// what it reads to the frames it gives back.
pub struct Timestretch {
    speed: f32,
    window: f32,
    rate: f32,
    history: History,
    head: Head,
    stretch: Option<Stretch>,
    stretch_rate: f32,
    owed: f64,
    input: Vec<f32>,
    stand: usize,
}

impl Timestretch {
    const SPECS: &'static [ParamSpec] = Varispeed::SPECS;

    pub fn new() -> Self {
        Timestretch {
            speed: 1.0,
            window: 1000.0,
            rate: 0.0,
            history: History::new(),
            head: Head::new(),
            stretch: None,
            stretch_rate: 0.0,
            owed: 0.0,
            input: vec![0.0; 4 * CHUNK],
            stand: 0,
        }
    }
}

impl Effect for Timestretch {
    fn name(&self) -> &'static str {
        "timestretch"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.speed = value,
            _ => self.window = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        // under the rack's lowest rate there is no stretcher, and sound passes through
        if rate.is_nan() || rate < MIN_RATE {
            self.stretch = None;
            return;
        }
        match &mut self.stretch {
            Some(s) if self.stretch_rate == rate => s.reset(),
            _ => {
                self.stretch = Some(Stretch::new(2, rate, 21));
                self.stretch_rate = rate;
            }
        }
        if let Some(s) = &mut self.stretch {
            s.set_transpose(1.0);
        }
        let splice = frames(SPLICE_MS, rate).max(1);
        // the head starts this far behind, so at speed 1 it never runs short mid-chunk and a
        // jump back always leaves the old head a splice of input to fade out on
        self.stand = 2 * splice + CHUNK;
        self.history
            .reset(frames(WINDOW_MAX_MS, rate) + self.stand + splice + 4 * CHUNK);
        self.head.reset(self.stand as f64, splice);
        self.owed = 0.0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        let Some(s) = &mut self.stretch else { return };
        let window = frames(self.window, self.rate) as f64;
        let margin = 2 * self.head.splice;
        for chunk in block.chunks_mut(CHUNK) {
            for f in chunk.iter() {
                self.history.push(*f);
            }
            let n = chunk.len();
            self.owed += self.speed as f64 * n as f64;
            let m = self.owed.floor() as usize;
            self.owed -= m as f64;
            let oldest = self.history.oldest() + self.stand as f64;
            self.head
                .drift(n as f64, (m + margin) as f64, window, oldest);
            for k in 0..m {
                let w = self.head.weight();
                let y = self.history.at(self.head.delay);
                let y = if w > 0.0 {
                    mix(y, self.history.at(self.head.old), w)
                } else {
                    y
                };
                self.input[2 * k..2 * k + 2].copy_from_slice(&y);
                self.head.delay -= 1.0;
                self.head.old -= 1.0;
            }
            s.process(&self.input[..2 * m], chunk.as_flattened_mut());
        }
    }
    fn latency(&self) -> usize {
        self.stand + self.stretch.as_ref().map_or(0, Stretch::latency)
    }
}

/// A tape slowing to a stop, or spinning up from one.
pub struct TapeMotion {
    starting: bool,
    time: f32,
    duration: f32,
    curve: f32,
    resume: f32,
    rate: f32,
    history: History,
    delay: f64,
    now: u64,
    fade: usize,
    splice: usize,
    synced: bool,
}

impl TapeMotion {
    const DURATION_MAX: f32 = 4.0;
    const START_SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("time", 0.0, 600.0, 0.0, "s"),
        ParamSpec::range("duration", 0.05, Self::DURATION_MAX, 0.5, "s"),
        ParamSpec::range("curve", 0.25, 4.0, 1.0, ""),
    ];
    const STOP_SPECS: &'static [ParamSpec] = &[
        Self::START_SPECS[0],
        Self::START_SPECS[1],
        Self::START_SPECS[2],
        ParamSpec::range("resume", 0.0, 600.0, 0.0, "s"),
    ];

    fn new(starting: bool) -> Self {
        TapeMotion {
            starting,
            time: 0.0,
            duration: 0.5,
            curve: 1.0,
            resume: 0.0,
            rate: 0.0,
            history: History::new(),
            delay: 0.0,
            now: 0,
            fade: 0,
            splice: 1,
            synced: false,
        }
    }

    pub fn stop() -> Self {
        Self::new(false)
    }

    pub fn start() -> Self {
        Self::new(true)
    }
}

impl Effect for TapeMotion {
    fn name(&self) -> &'static str {
        if self.starting {
            "tape_start"
        } else {
            "tape_stop"
        }
    }
    fn params(&self) -> &'static [ParamSpec] {
        if self.starting {
            Self::START_SPECS
        } else {
            Self::STOP_SPECS
        }
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), self.params(), name, value)? {
            0 => self.time = value,
            1 => self.duration = value,
            2 => self.curve = value,
            _ => self.resume = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.history
            .reset(frames(1000.0 * Self::DURATION_MAX + 2.0 * SPLICE_MS, rate) + 8);
        self.delay = 0.0;
        self.now = 0;
        self.fade = 0;
        self.splice = frames(SPLICE_MS, rate).max(1);
        self.synced = false;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.history.ready() {
            return;
        }
        let (rate, duration) = (self.rate as f64, self.duration as f64);
        // the edges of the motion, in fractions of it: a splice long, at most a quarter
        let edge = (SPLICE_MS as f64 * 0.001 / duration).min(0.25);
        for f in block {
            self.history.push(*f);
            let u = (self.now as f64 / rate - self.time as f64) / duration;
            self.now += 1;
            if !self.starting {
                if u < 0.0 {
                    continue;
                }
                if u >= 1.0 {
                    let back = (u - 1.0) * duration - self.resume as f64;
                    if self.resume > 0.0 && back >= 0.0 {
                        let gain = (back * rate + 1.0) / (self.splice + 1) as f64;
                        if gain < 1.0 {
                            *f = scale(*f, gain as f32);
                        }
                    } else {
                        *f = [0.0; 2];
                    }
                    continue;
                }
                let gain = ((1.0 - u) / edge).min(1.0) as f32;
                *f = scale(self.history.at(self.delay), gain);
                self.delay += 1.0 - (1.0 - u).powf(self.curve as f64);
            } else if u < 0.0 {
                *f = [0.0; 2];
            } else if u < 1.0 {
                let gain = (u / edge).min(1.0) as f32;
                *f = scale(self.history.at(self.delay), gain);
                self.delay += 1.0 - u.powf(self.curve as f64);
            } else {
                if !self.synced {
                    self.synced = true;
                    self.fade = self.splice;
                }
                if self.fade > 0 {
                    let w = self.fade as f32 / (self.splice + 1) as f32;
                    self.fade -= 1;
                    *f = mix(*f, self.history.at(self.delay), w);
                }
            }
        }
    }
}

/// Windows played backwards, one window late.
pub struct Reverse {
    window: f32,
    fade: f32,
    rate: f32,
    buf: Vec<[f32; 2]>,
    half: usize,
    front: usize,
    len: usize,
    played: usize,
    pos: usize,
}

impl Reverse {
    const WINDOW_MAX: f32 = 2000.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("window", 20.0, Self::WINDOW_MAX, 250.0, "ms"),
        ParamSpec::range("fade", 0.0, 20.0, 5.0, "ms"),
    ];

    pub fn new() -> Self {
        Reverse {
            window: 250.0,
            fade: 5.0,
            rate: 0.0,
            buf: Vec::new(),
            half: 0,
            front: 0,
            len: 0,
            played: 0,
            pos: 0,
        }
    }

    fn window_frames(&self) -> usize {
        frames(self.window, self.rate).clamp(1, self.half.max(1))
    }
}

impl Effect for Reverse {
    fn name(&self) -> &'static str {
        "reverse"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.window = value,
            _ => self.fade = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.half = frames(Self::WINDOW_MAX, rate).max(1);
        self.buf.clear();
        self.buf.resize(2 * self.half, [0.0; 2]);
        self.front = 0;
        self.len = self.window_frames();
        self.played = self.len;
        self.pos = 0;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if self.buf.is_empty() {
            return;
        }
        let mut fade = frames(self.fade, self.rate).min(self.played / 2);
        for f in block {
            if self.pos == self.len {
                self.front ^= 1;
                self.played = self.len;
                self.len = self.window_frames();
                self.pos = 0;
                fade = frames(self.fade, self.rate).min(self.played / 2);
            }
            let (pos, played) = (self.pos, self.played);
            let y = if pos < played {
                let y = self.buf[(1 - self.front) * self.half + played - 1 - pos];
                if fade == 0 {
                    y
                } else {
                    let edge = (pos + 1).min(played - pos) as f32 / (fade + 1) as f32;
                    scale(y, edge.min(1.0))
                }
            } else {
                [0.0; 2]
            };
            self.buf[self.front * self.half + pos] = *f;
            *f = y;
            self.pos += 1;
        }
    }
    fn latency(&self) -> usize {
        frames(self.window, self.rate)
    }
    fn offline_only(&self) -> bool {
        true
    }
}

/// The last stretch of input looped while held.
pub struct Freeze {
    hold: bool,
    length: f32,
    rate: f32,
    history: History,
    grain: Vec<[f32; 2]>,
    len: usize,
    cross: usize,
    pos: usize,
    looping: bool,
    wet: f32,
    step: f32,
}

impl Freeze {
    const LENGTH_MAX: f32 = 1000.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::choice("hold", ON_OFF),
        ParamSpec::range("length", 20.0, Self::LENGTH_MAX, 150.0, "ms"),
    ];

    pub fn new() -> Self {
        Freeze {
            hold: false,
            length: 150.0,
            rate: 0.0,
            history: History::new(),
            grain: Vec::new(),
            len: 0,
            cross: 0,
            pos: 0,
            looping: false,
            wet: 0.0,
            step: 1.0,
        }
    }

    /// The loop at `p`: its last quarter fades into the frames just before its start.
    fn looped(&self, p: usize) -> [f32; 2] {
        let body = self.len - self.cross;
        if p < body {
            return self.grain[p + self.cross];
        }
        let a = (p - body + 1) as f32 / (self.cross + 1) as f32;
        mix(self.grain[p + self.cross], self.grain[p - body], a)
    }
}

impl Effect for Freeze {
    fn name(&self) -> &'static str {
        "freeze"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.hold = on(value),
            _ => self.length = value,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        let most = frames(1.25 * Self::LENGTH_MAX, rate) + 4;
        self.history.reset(most);
        self.grain.clear();
        self.grain.resize(most, [0.0; 2]);
        self.looping = false;
        self.wet = 0.0;
        self.pos = 0;
        self.step = 1.0 / frames(5.0, rate).max(1) as f32;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.history.ready() {
            return;
        }
        for f in block {
            if self.hold && !self.looping {
                self.len = frames(self.length, self.rate).max(4);
                self.cross = self.len / 4;
                let total = self.len + self.cross;
                for i in 0..total {
                    self.grain[i] = self.history.back(total - 1 - i);
                }
                self.pos = 0;
                self.looping = true;
            }
            self.history.push(*f);
            self.wet = if self.hold {
                (self.wet + self.step).min(1.0)
            } else {
                (self.wet - self.step).max(0.0)
            };
            if self.looping {
                *f = mix(*f, self.looped(self.pos), self.wet);
                self.pos = (self.pos + 1) % self.len;
                if !self.hold && self.wet == 0.0 {
                    self.looping = false;
                }
            }
        }
    }
}

#[derive(Clone, Copy)]
struct Grain {
    delay: f64,
    drift: f64,
    age: usize,
    len: usize,
}

const MAX_GRAINS: usize = 64;

/// Hann grains around a moving position.
pub struct Granular {
    size: f32,
    density: f32,
    scatter: f32,
    jitter: f32,
    stretch: f32,
    seed: u64,
    rate: f32,
    history: History,
    head: f64,
    until: f64,
    random: Random,
    grains: [Grain; MAX_GRAINS],
}

impl Granular {
    /// How far back the position may lag before it jumps forward, and loops back when ahead.
    const SCAN_MS: f32 = 2000.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("size", 10.0, 500.0, 80.0, "ms"),
        ParamSpec::range("density", 1.0, 100.0, 20.0, "/s"),
        ParamSpec::range("scatter", 0.0, 500.0, 0.0, "ms"),
        ParamSpec::range("jitter", 0.0, 12.0, 0.0, "st"),
        ParamSpec::range("stretch", 0.0, 2.0, 1.0, "x"),
        ParamSpec::range("seed", 0.0, 65535.0, 0.0, ""),
    ];
    const FREE: Grain = Grain {
        delay: 0.0,
        drift: 0.0,
        age: 0,
        len: 0,
    };

    pub fn new() -> Self {
        Granular {
            size: 80.0,
            density: 20.0,
            scatter: 0.0,
            jitter: 0.0,
            stretch: 1.0,
            seed: 0,
            rate: 0.0,
            history: History::new(),
            head: 0.0,
            until: 0.0,
            random: Random(0),
            grains: [Self::FREE; MAX_GRAINS],
        }
    }

    fn spawn(&mut self) {
        let (a, b) = (self.random.signed(), self.random.signed());
        let len = frames(self.size, self.rate).max(2);
        let speed = ratio(self.jitter * a) as f64;
        let scatter = frames(self.scatter, self.rate) as f64 * (b as f64 + 1.0) * 0.5;
        // a grain faster than the input starts far enough back not to overtake it
        let delay = self.head + scatter + ((speed - 1.0) * len as f64).max(0.0);
        if let Some(g) = self.grains.iter_mut().find(|g| g.len == 0) {
            *g = Grain {
                delay,
                drift: 1.0 - speed,
                age: 0,
                len,
            };
        }
    }
}

impl Effect for Granular {
    fn name(&self) -> &'static str {
        "granular"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.size = value,
            1 => self.density = value,
            2 => self.scatter = value,
            3 => self.jitter = value,
            4 => self.stretch = value,
            _ => self.seed = value.round() as u64,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        // the scan, the widest scatter, a grain's start ahead and its lag at half speed
        self.history
            .reset(frames(Self::SCAN_MS + 500.0 + 500.0 + 250.0, rate) + 8);
        self.head = 0.0;
        self.until = 0.0;
        self.random = Random(self.seed);
        self.grains = [Self::FREE; MAX_GRAINS];
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.history.ready() {
            return;
        }
        let scan = frames(Self::SCAN_MS, self.rate) as f64;
        let hop = self.rate as f64 / self.density as f64;
        // Hann grains average a half: this many overlap on average
        let gain = 1.0 / (0.5 * self.density * self.size * 0.001).max(1.0);
        for f in block {
            self.history.push(*f);
            self.head += 1.0 - self.stretch as f64;
            if self.head < 0.0 {
                self.head = (self.head + scan).min(self.history.oldest());
            } else if self.head > scan {
                self.head -= scan;
            }
            while self.until <= 0.0 {
                self.spawn();
                self.until += hop;
            }
            self.until -= 1.0;
            let mut sum = [0.0f32; 2];
            for g in self.grains.iter_mut().filter(|g| g.len > 0) {
                let w = 0.5 - 0.5 * (2.0 * PI * g.age as f32 / g.len as f32).cos();
                let y = self.history.at(g.delay);
                sum[0] += w * y[0];
                sum[1] += w * y[1];
                g.delay += g.drift;
                g.age += 1;
                if g.age == g.len {
                    g.len = 0;
                }
            }
            *f = scale(sum, gain);
        }
    }
}

/// The last slice repeated in place of the input when triggered.
pub struct Stutter {
    length: f32,
    repeats: usize,
    fall: f32,
    step: f32,
    trigger: bool,
    pending: bool,
    rate: f32,
    history: History,
    slice: Vec<[f32; 2]>,
    len: usize,
    total: usize,
    left: usize,
    fade: usize,
    dry: usize,
}

impl Stutter {
    const LENGTH_MAX: f32 = 1000.0;
    const SPECS: &'static [ParamSpec] = &[
        ParamSpec::range("length", 10.0, Self::LENGTH_MAX, 125.0, "ms"),
        ParamSpec::range("repeats", 1.0, 32.0, 4.0, ""),
        ParamSpec::range("fall", 0.0, 24.0, 0.0, "dB"),
        ParamSpec::range("step", -12.0, 12.0, 0.0, "st"),
        ParamSpec::choice("trigger", ON_OFF),
    ];

    pub fn new() -> Self {
        Stutter {
            length: 125.0,
            repeats: 4,
            fall: 0.0,
            step: 0.0,
            trigger: false,
            pending: false,
            rate: 0.0,
            history: History::new(),
            slice: Vec::new(),
            len: 0,
            total: 0,
            left: 0,
            fade: 1,
            dry: 1,
        }
    }

    /// Frame `pos` of repeat `k`, enveloped so cuts do not click.
    fn repeat(&self, k: usize, pos: usize) -> [f32; 2] {
        let src = pos as f32 * ratio(self.step * k as f32);
        let last = (self.len - 1) as f32;
        if src > last {
            return [0.0; 2];
        }
        let i = src as usize;
        let t = src - i as f32;
        let y = mix(self.slice[i], self.slice[i + 1], t);
        let fade = (self.fade + 1) as f32;
        let env = ((src + 1.0) / fade)
            .min((self.len as f32 - src) / fade)
            .min((self.len - pos) as f32 / fade)
            .min(1.0);
        scale(y, env * amplitude(-self.fall * k as f32))
    }
}

impl Effect for Stutter {
    fn name(&self) -> &'static str {
        "stutter"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.length = value,
            1 => self.repeats = value.round() as usize,
            2 => self.fall = value,
            3 => self.step = value,
            _ => {
                let up = on(value);
                self.pending |= up && !self.trigger;
                self.trigger = up;
            }
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        let most = frames(Self::LENGTH_MAX, rate) + 4;
        self.history.reset(most);
        self.slice.clear();
        self.slice.resize(most, [0.0; 2]);
        self.pending = false;
        self.left = 0;
        self.fade = frames(2.0, rate).max(1);
        self.dry = self.fade;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.history.ready() {
            return;
        }
        for f in block {
            if self.pending {
                self.pending = false;
                self.len = frames(self.length, self.rate).max(2);
                for i in 0..self.len {
                    self.slice[i] = self.history.back(self.len - 1 - i);
                }
                self.total = self.repeats * self.len;
                self.left = self.total;
            }
            self.history.push(*f);
            let active = self.left > 0;
            self.dry = if active {
                self.dry.saturating_sub(1)
            } else {
                (self.dry + 1).min(self.fade)
            };
            let mut y = scale(*f, self.dry as f32 / self.fade as f32);
            if active {
                let done = self.total - self.left;
                let w = self.repeat(done / self.len, done % self.len);
                y = [y[0] + w[0], y[1] + w[1]];
                self.left -= 1;
            }
            *f = y;
        }
    }
}

/// Sixteenth notes at a tempo, counted from reset; a change of tempo starts a new count.
struct Grid {
    bpm: f32,
    rate: f32,
    slice: f64,
    origin: u64,
    step: u64,
    start: u64,
    end: u64,
    now: u64,
    rebase: bool,
}

impl Grid {
    fn new(bpm: f32) -> Self {
        Grid {
            bpm,
            rate: 0.0,
            slice: 1.0,
            origin: 0,
            step: 0,
            start: 0,
            end: 0,
            now: 0,
            rebase: true,
        }
    }

    fn set_bpm(&mut self, bpm: f32) {
        if bpm != self.bpm {
            self.bpm = bpm;
            self.rebase = true;
        }
    }

    fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.now = 0;
        self.rebase = true;
    }

    /// Where sixteenth `step` of the current count begins.
    fn boundary(&self, step: u64) -> u64 {
        self.origin + (step as f64 * self.slice).round() as u64
    }

    /// Called at each frame before it is used; true when the frame begins a sixteenth.
    fn tick(&mut self) -> bool {
        if self.rebase {
            self.rebase = false;
            self.slice = self.rate as f64 * 15.0 / self.bpm as f64;
            self.origin = self.now;
            self.step = 0;
        } else if self.now == self.end {
            self.step += 1;
        } else {
            return false;
        }
        self.start = self.boundary(self.step);
        self.end = self.boundary(self.step + 1);
        true
    }

    fn advance(&mut self) {
        self.now += 1;
    }
}

const BPM: ParamSpec = ParamSpec::range("bpm", 60.0, 200.0, 90.0, "bpm");
const STEP_NAMES: [&str; 16] = [
    "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13", "s14",
    "s15",
];

const fn slot(i: usize) -> ParamSpec {
    ParamSpec::range(STEP_NAMES[i], 0.0, 15.0, i as f32, "")
}

const fn gate_step(i: usize) -> ParamSpec {
    ParamSpec {
        default: if i.is_multiple_of(2) { 1.0 } else { 0.0 },
        ..ParamSpec::choice(STEP_NAMES[i], ON_OFF)
    }
}

/// Sixteenths re-ordered by a pattern.
pub struct Chop {
    grid: Grid,
    steps: usize,
    pattern: [usize; 16],
    history: History,
    fade: usize,
    src: Option<u64>,
    fade_in: bool,
    fade_out: bool,
}

impl Chop {
    const SPECS: &'static [ParamSpec] = &[
        BPM,
        ParamSpec::range("steps", 1.0, 16.0, 8.0, ""),
        slot(0),
        slot(1),
        slot(2),
        slot(3),
        slot(4),
        slot(5),
        slot(6),
        slot(7),
        slot(8),
        slot(9),
        slot(10),
        slot(11),
        slot(12),
        slot(13),
        slot(14),
        slot(15),
    ];

    pub fn new() -> Self {
        let mut pattern = [0; 16];
        for (i, p) in pattern.iter_mut().enumerate() {
            *p = i;
        }
        Chop {
            grid: Grid::new(BPM.default),
            steps: 8,
            pattern,
            history: History::new(),
            fade: 1,
            src: None,
            fade_in: false,
            fade_out: false,
        }
    }

    /// The sixteenth that sixteenth `q` plays; before the start each plays itself.
    fn source(&self, q: i64) -> Option<i64> {
        if q < 0 {
            return Some(q);
        }
        let s = self.steps as i64;
        let (c, j) = (q / s, q % s);
        let k = (self.pattern[j as usize] % self.steps) as i64;
        if k <= j {
            Some(c * s + k)
        } else if c > 0 {
            Some((c - 1) * s + k)
        } else {
            None
        }
    }
}

impl Effect for Chop {
    fn name(&self) -> &'static str {
        "chop"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.grid.set_bpm(value),
            1 => self.steps = value.round() as usize,
            i => self.pattern[i - 2] = value.round() as usize,
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.grid.reset(rate);
        // two cycles of the longest sixteenths: a slice may come from the cycle before
        let longest = rate as f64 * 15.0 / BPM.low as f64;
        self.history.reset((32.0 * longest).ceil() as usize + 8);
        self.fade = frames(3.0, rate).max(1);
        self.src = None;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        if !self.history.ready() {
            return;
        }
        for f in block {
            if self.grid.tick() {
                let q = self.grid.step as i64;
                let src = self.source(q);
                let follows = |a: Option<i64>, b: Option<i64>| matches!((a, b), (Some(a), Some(b)) if b == a + 1);
                self.fade_in = !follows(self.source(q - 1), src);
                self.fade_out = !follows(src, self.source(q + 1));
                self.src = src.map(|s| self.grid.boundary(s as u64));
            }
            self.history.push(*f);
            let grid = &self.grid;
            let (pos, len) = (grid.now - grid.start, grid.end - grid.start);
            *f = match self.src {
                None => [0.0; 2],
                Some(from) => {
                    let y = self.history.back((grid.now - from - pos) as usize);
                    let fade = self.fade.min(len as usize / 4) as u64 + 1;
                    let mut env = 1.0f32;
                    if self.fade_in {
                        env = env.min((pos + 1) as f32 / fade as f32);
                    }
                    if self.fade_out {
                        env = env.min((len - pos) as f32 / fade as f32);
                    }
                    if env < 1.0 {
                        scale(y, env)
                    } else {
                        y
                    }
                }
            };
            self.grid.advance();
        }
    }
}

/// Sixteenths turned on and off.
pub struct TranceGate {
    grid: Grid,
    attack: f32,
    release: f32,
    depth: f32,
    pattern: [bool; 16],
    gain: f32,
    open: bool,
}

impl TranceGate {
    const SPECS: &'static [ParamSpec] = &[
        BPM,
        ParamSpec::range("attack", 0.0, 100.0, 2.0, "ms"),
        ParamSpec::range("release", 0.0, 200.0, 10.0, "ms"),
        ParamSpec::range("depth", 0.0, 100.0, 100.0, "dB"),
        gate_step(0),
        gate_step(1),
        gate_step(2),
        gate_step(3),
        gate_step(4),
        gate_step(5),
        gate_step(6),
        gate_step(7),
        gate_step(8),
        gate_step(9),
        gate_step(10),
        gate_step(11),
        gate_step(12),
        gate_step(13),
        gate_step(14),
        gate_step(15),
    ];

    pub fn new() -> Self {
        let mut pattern = [false; 16];
        for (i, p) in pattern.iter_mut().enumerate() {
            *p = i.is_multiple_of(2);
        }
        TranceGate {
            grid: Grid::new(BPM.default),
            attack: 2.0,
            release: 10.0,
            depth: 100.0,
            pattern,
            gain: 1.0,
            open: true,
        }
    }
}

impl Effect for TranceGate {
    fn name(&self) -> &'static str {
        "trance_gate"
    }
    fn params(&self) -> &'static [ParamSpec] {
        Self::SPECS
    }
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError> {
        match check(self.name(), Self::SPECS, name, value)? {
            0 => self.grid.set_bpm(value),
            1 => self.attack = value,
            2 => self.release = value,
            3 => self.depth = value,
            i => self.pattern[i - 4] = on(value),
        }
        Ok(())
    }
    fn reset(&mut self, rate: f32) {
        self.grid.reset(rate);
        self.gain = 1.0;
        self.open = true;
    }
    fn process(&mut self, block: &mut [[f32; 2]]) {
        let rate = self.grid.rate;
        if rate <= 0.0 {
            return;
        }
        let floor = amplitude(-self.depth);
        let up = (1.0 - floor) / frames(self.attack, rate).max(1) as f32;
        let down = (1.0 - floor) / frames(self.release, rate).max(1) as f32;
        for f in block {
            if self.grid.tick() {
                self.open = self.pattern[(self.grid.step % 16) as usize];
            }
            let target = if self.open { 1.0 } else { floor };
            if self.gain < target {
                self.gain = (self.gain + up).min(target);
            } else if self.gain > target {
                self.gain = (self.gain - down).max(target);
            }
            *f = scale(*f, self.gain);
            self.grid.advance();
        }
    }
}
