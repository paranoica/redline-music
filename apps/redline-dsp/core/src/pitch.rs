//! Pitch: a streaming YIN detector, notes of a key and scale, and a tracker that lines the
//! detections up with a pitch shifter's analysis time.

pub const KEYS: &[&str] = &[
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
];

pub const SCALES: &[&str] = &[
    "major",
    "minor",
    "harmonic minor",
    "phrygian",
    "dorian",
    "chromatic",
];

const STEPS: [&[i32]; 6] = [
    &[0, 2, 4, 5, 7, 9, 11],
    &[0, 2, 3, 5, 7, 8, 10],
    &[0, 2, 3, 5, 7, 8, 11],
    &[0, 1, 3, 5, 7, 8, 10],
    &[0, 2, 3, 5, 7, 9, 10],
    &[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
];

/// MIDI note of a frequency, with A4 tuned `reference` cents away from 440 Hz.
pub fn midi(freq: f32, reference: f32) -> f32 {
    69.0 + 12.0 * (freq / 440.0).log2() - reference / 100.0
}

/// Frequency of a (fractional) MIDI note, with A4 tuned `reference` cents away from 440 Hz.
pub fn freq(midi: f32, reference: f32) -> f32 {
    440.0 * 2f32.powf((midi - 69.0) / 12.0 + reference / 1200.0)
}

/// The notes of one scale in one key.
#[derive(Clone, Copy, Debug)]
pub struct Scale {
    key: i32,
    steps: &'static [i32],
}

impl Scale {
    /// `key` indexes [`KEYS`], `scale` indexes [`SCALES`]; out of range wraps or clamps.
    pub fn new(key: usize, scale: usize) -> Scale {
        Scale {
            key: (key % 12) as i32,
            steps: STEPS[scale.min(STEPS.len() - 1)],
        }
    }

    /// The scale note nearest a fractional MIDI note.
    pub fn nearest(&self, midi: f32) -> i32 {
        let octave = ((midi - self.key as f32) / 12.0).floor() as i32;
        let mut best = (f32::INFINITY, 0);
        for o in octave - 1..=octave + 1 {
            for s in self.steps {
                let note = self.key + 12 * o + s;
                let distance = (note as f32 - midi).abs();
                if distance < best.0 {
                    best = (distance, note);
                }
            }
        }
        best.1
    }

    fn degree(&self, note: i32) -> i32 {
        let rel = note - self.key;
        let len = self.steps.len() as i32;
        let index = self
            .steps
            .iter()
            .position(|&s| s == rel.rem_euclid(12))
            .unwrap_or(0) as i32;
        rel.div_euclid(12) * len + index
    }

    fn note(&self, degree: i32) -> i32 {
        let len = self.steps.len() as i32;
        self.key + 12 * degree.div_euclid(len) + self.steps[degree.rem_euclid(len) as usize]
    }

    /// Semitones from a scale note to the note `degrees` scale steps away.
    pub fn interval(&self, note: i32, degrees: i32) -> i32 {
        self.note(self.degree(note) + degrees) - note
    }
}

const LOWEST: f32 = 60.0;
const HIGHEST: f32 = 1500.0;
const WINDOW: f32 = 0.025;
const HOP: f32 = 0.01;
const THRESHOLD: f32 = 0.15;
// about -70 dBFS: quieter frames are called unvoiced
const QUIET: f32 = 1e-7;

/// YIN (de Cheveigné and Kawahara, 2002) over the last ~42 ms, every 10 ms.
pub struct Yin {
    rate: f32,
    window: usize,
    min_lag: usize,
    max_lag: usize,
    hop: usize,
    buf: Vec<f32>,
    fill: usize,
    diff: Vec<f32>,
    freq: Option<f32>,
}

impl Default for Yin {
    fn default() -> Self {
        Self::new()
    }
}

impl Yin {
    /// Unsized until `reset`.
    pub fn new() -> Yin {
        Yin {
            rate: 0.0,
            window: 0,
            min_lag: 0,
            max_lag: 0,
            hop: 0,
            buf: Vec::new(),
            fill: 0,
            diff: Vec::new(),
            freq: None,
        }
    }

    pub fn reset(&mut self, rate: f32) {
        self.rate = rate;
        self.window = (rate * WINDOW).round() as usize;
        self.min_lag = ((rate / HIGHEST).floor() as usize).max(2);
        self.max_lag = (rate / LOWEST).ceil() as usize;
        self.hop = ((rate * HOP).round() as usize).max(1);
        self.buf = vec![0.0; self.window + self.max_lag + 1];
        // primed with silence so detections come every hop from the first sample on
        self.fill = self.buf.len() - self.hop;
        self.diff = vec![0.0; self.max_lag + 2];
        self.freq = None;
    }

    /// Samples between detections.
    pub fn hop(&self) -> usize {
        self.hop
    }

    /// Samples one detection looks at; its moment is half of this before the newest sample.
    pub fn span(&self) -> usize {
        self.buf.len()
    }

    /// Samples still to push before the next detection.
    pub fn until_detection(&self) -> usize {
        self.buf.len() - self.fill
    }

    /// Takes one sample; true when it completed a detection.
    pub fn push(&mut self, x: f32) -> bool {
        if self.buf.is_empty() {
            return false;
        }
        self.buf[self.fill] = x;
        self.fill += 1;
        if self.fill < self.buf.len() {
            return false;
        }
        self.freq = self.detect();
        self.buf.copy_within(self.hop.., 0);
        self.fill = self.buf.len() - self.hop;
        true
    }

    /// The latest detection; None when unvoiced or too quiet.
    pub fn freq(&self) -> Option<f32> {
        self.freq
    }

    fn detect(&mut self) -> Option<f32> {
        let (w, buf) = (self.window, &self.buf);
        let energy = buf.iter().map(|v| v * v).sum::<f32>() / buf.len() as f32;
        // NaN input counts as quiet too
        if energy.is_nan() || energy <= QUIET {
            return None;
        }
        for tau in 1..=self.max_lag + 1 {
            self.diff[tau] = squared_distance(&buf[..w], &buf[tau..tau + w]);
        }
        let d = &self.diff;
        let mut sum = 0.0;
        let mut found = None;
        let mut tau = 1;
        while tau <= self.max_lag {
            sum += d[tau];
            let norm = |t: usize, s: f32| d[t] * t as f32 / s.max(f32::MIN_POSITIVE);
            if tau >= self.min_lag && norm(tau, sum) < THRESHOLD {
                // walk down to the bottom of this dip
                let mut s = sum;
                while tau < self.max_lag && norm(tau + 1, s + d[tau + 1]) < norm(tau, s) {
                    tau += 1;
                    s += d[tau];
                }
                found = Some(tau);
                break;
            }
            tau += 1;
        }
        let tau = found?;
        let (a, b, c) = (d[tau - 1], d[tau], d[tau + 1]);
        let bend = a - 2.0 * b + c;
        let shift = if bend > 0.0 {
            (0.5 * (a - c) / bend).clamp(-1.0, 1.0)
        } else {
            0.0
        };
        Some(self.rate / (tau as f32 + shift))
    }
}

/// Sum of squared differences, in eight lanes so it vectorises without reassociating floats.
fn squared_distance(a: &[f32], b: &[f32]) -> f32 {
    let mut lanes = [0.0f32; 8];
    let (ca, cb) = (a.chunks_exact(8), b.chunks_exact(8));
    let (ra, rb) = (ca.remainder(), cb.remainder());
    for (x, y) in ca.zip(cb) {
        for k in 0..8 {
            let e = x[k] - y[k];
            lanes[k] += e * e;
        }
    }
    let mut total: f32 = lanes.iter().sum();
    for (x, y) in ra.iter().zip(rb) {
        total += (x - y) * (x - y);
    }
    total
}

/// YIN whose detections come out delayed to describe the moment a shifter analyses now.
pub struct Tracker {
    yin: Yin,
    history: Vec<f32>,
    pos: usize,
}

impl Default for Tracker {
    fn default() -> Self {
        Self::new()
    }
}

impl Tracker {
    pub fn new() -> Tracker {
        Tracker {
            yin: Yin::new(),
            history: Vec::new(),
            pos: 0,
        }
    }

    /// `behind`: how many samples before the newest input the shifter's analysis is centred.
    pub fn reset(&mut self, rate: f32, behind: usize) {
        self.yin.reset(rate);
        let hop = self.yin.hop();
        let delay = (behind.saturating_sub(self.yin.span() / 2) + hop / 2) / hop;
        self.history = vec![0.0; delay + 1];
        self.pos = 0;
    }

    pub fn hop(&self) -> usize {
        self.yin.hop()
    }

    pub fn until_detection(&self) -> usize {
        self.yin.until_detection()
    }

    /// Takes one sample; true when a new (delayed) detection is ready in `freq`.
    pub fn push(&mut self, x: f32) -> bool {
        if !self.yin.push(x) || self.history.is_empty() {
            return false;
        }
        self.history[self.pos] = self.yin.freq().unwrap_or(0.0);
        self.pos = (self.pos + 1) % self.history.len();
        true
    }

    /// The detection for the analysed moment; None when unvoiced.
    pub fn freq(&self) -> Option<f32> {
        let f = *self.history.get(self.pos)?;
        (f > 0.0).then_some(f)
    }
}
