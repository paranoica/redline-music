//! A beat's score, read from JSON by hand so every problem comes back at once, each with the
//! path of the value at fault.

use super::instruments::{instrument, Instrument, Kind};
use crate::effect::{check, make, Rack};
use serde_json::{json, Map, Value};
use std::fmt;

pub const STEPS_PER_BAR: u32 = 16;
pub const MAX_SOUNDS: usize = 16;
pub const MAX_PATTERNS: usize = 64;
pub const MAX_NOTES: usize = 512;
pub const MAX_BARS: u32 = 512;
pub const MAX_RACK: usize = 4;
pub const BPM: (f64, f64) = (40.0, 220.0);
pub const SWING: (f64, f64) = (50.0, 75.0);
const PATTERN_BARS: [u32; 4] = [1, 2, 4, 8];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ScoreError {
    pub path: String,
    pub message: String,
}

impl fmt::Display for ScoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.path.is_empty() {
            f.write_str(&self.message)
        } else {
            write!(f, "{}: {}", self.path, self.message)
        }
    }
}

impl std::error::Error for ScoreError {}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Major,
    Minor,
    Dorian,
    Phrygian,
    Lydian,
    Mixolydian,
    Locrian,
    HarmonicMinor,
}

const MODES: [(&str, Mode); 8] = [
    ("major", Mode::Major),
    ("minor", Mode::Minor),
    ("dorian", Mode::Dorian),
    ("phrygian", Mode::Phrygian),
    ("lydian", Mode::Lydian),
    ("mixolydian", Mode::Mixolydian),
    ("locrian", Mode::Locrian),
    ("harmonic_minor", Mode::HarmonicMinor),
];

impl Mode {
    pub fn name(self) -> &'static str {
        MODES
            .iter()
            .find(|(_, m)| *m == self)
            .map(|(name, _)| *name)
            .expect("every mode is named")
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Key {
    /// Pitch class, 0 for C.
    pub tonic: u8,
    pub mode: Mode,
}

#[derive(Clone, Debug)]
pub struct Sound {
    pub name: String,
    pub instrument: &'static Instrument,
    /// A value for every param of the instrument, in its order; the defaults where the score
    /// gives none.
    pub params: Vec<f32>,
    pub swing: Option<f64>,
    /// The sound library a sampler plays.
    pub library: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Hit {
    Rest,
    Hit,
    Accent,
    Ghost,
    /// That many even hits inside the step.
    Roll(u8),
}

impl Hit {
    pub fn hits(self) -> u32 {
        match self {
            Hit::Rest => 0,
            Hit::Roll(n) => n as u32,
            _ => 1,
        }
    }

    /// The velocity of the hit when the row has no velocity lane.
    pub fn velocity(self) -> f32 {
        match self {
            Hit::Rest => 0.0,
            Hit::Accent => 1.0,
            Hit::Ghost => 0.4,
            Hit::Hit | Hit::Roll(_) => 0.8,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct DrumRow {
    pub steps: Vec<Hit>,
    pub velocity: Option<Vec<f32>>,
    /// Semitones.
    pub pitch: Option<Vec<f32>>,
    /// A scale on the instrument's decay.
    pub decay: Option<Vec<f32>>,
    pub chance: Option<Vec<f32>>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Note {
    pub step: u32,
    pub len: u32,
    /// MIDI.
    pub note: u8,
    pub vel: f32,
    /// Allowed outside the key's scale.
    pub passing: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Row {
    Drum(DrumRow),
    Notes(Vec<Note>),
}

#[derive(Clone, Debug)]
pub struct Pattern {
    pub name: String,
    pub bars: u32,
    /// By sound index, in its order.
    pub rows: Vec<(usize, Row)>,
}

#[derive(Clone, Debug)]
pub struct Section {
    pub name: String,
    pub bars: u32,
    pub play: Vec<usize>,
    pub mute: Vec<usize>,
    pub fill: Option<usize>,
}

#[derive(Clone, Debug)]
pub struct SoundMix {
    pub gain_db: f32,
    pub pan: f32,
    /// The rack as [`Rack::from_json`] takes it.
    pub rack: String,
    pub sidechain: Option<usize>,
}

#[derive(Clone, Debug)]
pub struct Mix {
    /// One a sound, by sound index.
    pub sounds: Vec<SoundMix>,
    pub master: String,
}

/// Sounds and patterns are kept sorted by name, so their indices do not hang on the order
/// of the JSON object.
#[derive(Clone, Debug)]
pub struct Score {
    pub bpm: f64,
    pub swing: f64,
    pub seed: u32,
    pub key: Key,
    pub sounds: Vec<Sound>,
    pub patterns: Vec<Pattern>,
    pub sections: Vec<Section>,
    pub mix: Mix,
}

impl Score {
    pub fn from_json(text: &str) -> Result<Score, Vec<ScoreError>> {
        let value: Value = serde_json::from_str(text).map_err(|e| {
            vec![ScoreError {
                path: String::new(),
                message: format!("not JSON: {e}"),
            }]
        })?;
        Score::from_value(&value)
    }

    pub fn from_value(value: &Value) -> Result<Score, Vec<ScoreError>> {
        let mut p = Parser { errors: Vec::new() };
        let score = p.score(value);
        match score {
            Some(s) if p.errors.is_empty() => Ok(s),
            _ => Err(p.errors),
        }
    }

    pub fn sound(&self, name: &str) -> Option<usize> {
        self.sounds.iter().position(|s| s.name == name)
    }

    pub fn pattern(&self, name: &str) -> Option<usize> {
        self.patterns.iter().position(|s| s.name == name)
    }

    /// Bars of the whole song.
    pub fn bars(&self) -> u32 {
        self.sections.iter().map(|s| s.bars).sum()
    }
}

fn join(path: &str, key: &str) -> String {
    if path.is_empty() {
        key.to_string()
    } else {
        format!("{path}.{key}")
    }
}

fn index(path: &str, i: usize) -> String {
    format!("{path}[{i}]")
}

fn good_name(s: &str) -> bool {
    (1..=24).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
}

/// Section names are quoted back into the next agent request: plain text only.
fn good_section_name(s: &str) -> bool {
    (1..=48).contains(&s.len())
        && !s.starts_with(' ')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b' ' || b == b'_' || b == b'-')
}

fn good_library(s: &str) -> bool {
    (1..=48).contains(&s.len())
        && !s.starts_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_' || b == b'-')
}

fn article(bars: u32) -> &'static str {
    if bars == 8 {
        "an"
    } else {
        "a"
    }
}

/// The pitch class of a letter with an optional `#` or `b`, and the rest of the text.
fn pitch_class(s: &str) -> Option<(i32, &str)> {
    let mut chars = s.chars();
    let base = match chars.next()? {
        'C' => 0,
        'D' => 2,
        'E' => 4,
        'F' => 5,
        'G' => 7,
        'A' => 9,
        'B' => 11,
        _ => return None,
    };
    let rest = chars.as_str();
    Some(if let Some(r) = rest.strip_prefix('#') {
        (base + 1, r)
    } else if let Some(r) = rest.strip_prefix('b') {
        (base - 1, r)
    } else {
        (base, rest)
    })
}

/// The MIDI number of a name like `F#1`, `Bb2` or `C-1`, which may fall outside 0..=127.
fn note_number(s: &str) -> Option<i32> {
    let (pc, octave) = pitch_class(s)?;
    let digits = octave.strip_prefix('-').unwrap_or(octave);
    if digits.is_empty() || digits.len() > 2 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let octave: i32 = octave.parse().ok()?;
    Some((octave + 1) * 12 + pc)
}

fn parse_key(s: &str) -> Option<Key> {
    let (tonic, mode) = s.split_once(' ')?;
    let (pc, rest) = pitch_class(tonic)?;
    if !rest.is_empty() {
        return None;
    }
    let mode = MODES.iter().find(|(n, _)| *n == mode)?.1;
    Some(Key {
        tonic: pc.rem_euclid(12) as u8,
        mode,
    })
}

fn sorted(map: &Map<String, Value>) -> Vec<(&String, &Value)> {
    let mut entries: Vec<(&String, &Value)> = map.iter().collect();
    entries.sort_by(|a, b| a.0.cmp(b.0));
    entries
}

struct Parser {
    errors: Vec<ScoreError>,
}

/// A sound as far as it could be read; `instrument` is None when it named none known.
struct SoundDraft {
    name: String,
    instrument: Option<&'static Instrument>,
    params: Vec<f32>,
    swing: Option<f64>,
    library: Option<String>,
}

struct PatternDraft {
    name: String,
    bars: Option<u32>,
    rows: Vec<(usize, Row)>,
}

impl Parser {
    fn err(&mut self, path: &str, message: impl Into<String>) {
        self.errors.push(ScoreError {
            path: path.to_string(),
            message: message.into(),
        });
    }

    /// An object with only the `allowed` fields; each other field is an error.
    fn object<'a>(
        &mut self,
        v: &'a Value,
        path: &str,
        allowed: &[&str],
    ) -> Option<&'a Map<String, Value>> {
        let map = self.map(v, path)?;
        for (k, _) in sorted(map) {
            if !allowed.contains(&k.as_str()) {
                self.err(&join(path, k), "unknown field");
            }
        }
        Some(map)
    }

    fn map<'a>(&mut self, v: &'a Value, path: &str) -> Option<&'a Map<String, Value>> {
        let map = v.as_object();
        if map.is_none() {
            self.err(path, "expected an object");
        }
        map
    }

    fn array<'a>(&mut self, v: &'a Value, path: &str) -> Option<&'a Vec<Value>> {
        let a = v.as_array();
        if a.is_none() {
            self.err(path, "expected a list");
        }
        a
    }

    fn string<'a>(&mut self, v: &'a Value, path: &str) -> Option<&'a str> {
        let s = v.as_str();
        if s.is_none() {
            self.err(path, "expected a string");
        }
        s
    }

    fn required<'a>(
        &mut self,
        map: &'a Map<String, Value>,
        key: &str,
        path: &str,
    ) -> Option<&'a Value> {
        let v = map.get(key);
        if v.is_none() {
            self.err(&join(path, key), "missing");
        }
        v
    }

    fn number(&mut self, v: &Value, path: &str, low: f64, high: f64) -> Option<f64> {
        let Some(x) = v.as_f64() else {
            self.err(path, "expected a number");
            return None;
        };
        if !(low <= x && x <= high) {
            self.err(path, format!("{x} is outside {low}..{high}"));
            return None;
        }
        Some(x)
    }

    /// A whole number in `low..=high`; `2.0` counts as whole.
    fn whole(&mut self, v: &Value, path: &str, low: u64, high: u64) -> Option<u64> {
        let n = match v.as_u64() {
            Some(n) => Some(n),
            None => v
                .as_f64()
                .filter(|x| x.fract() == 0.0 && *x >= 0.0 && *x <= u64::MAX as f64)
                .map(|x| x as u64),
        };
        let Some(n) = n else {
            if v.is_number() {
                self.err(path, format!("{v} is not a whole number from {low}"));
            } else {
                self.err(path, "expected a whole number");
            }
            return None;
        };
        if !(low..=high).contains(&n) {
            self.err(path, format!("{n} is outside {low}..{high}"));
            return None;
        }
        Some(n)
    }

    fn score(&mut self, v: &Value) -> Option<Score> {
        const TOP: &[&str] = &[
            "version", "bpm", "swing", "seed", "key", "sounds", "patterns", "sections", "mix",
        ];
        let top = self.object(v, "", TOP)?;
        if let Some(version) = self.required(top, "version", "") {
            if version.as_u64() != Some(1) {
                self.err(
                    "version",
                    format!("version {version} is not known; this reads 1"),
                );
            }
        }
        let bpm = self
            .required(top, "bpm", "")
            .and_then(|v| self.number(v, "bpm", BPM.0, BPM.1));
        let swing = match top.get("swing") {
            Some(v) => self.number(v, "swing", SWING.0, SWING.1),
            None => Some(50.0),
        };
        let seed = self
            .required(top, "seed", "")
            .and_then(|v| self.whole(v, "seed", 0, u32::MAX as u64));
        let key = self.required(top, "key", "").and_then(|v| {
            let s = self.string(v, "key")?;
            let key = parse_key(s);
            if key.is_none() {
                self.err(
                    "key",
                    format!(
                        "{s:?} is not a key: a tonic like C, F# or Bb, a space and one of {}",
                        MODES.map(|m| m.0).join(", ")
                    ),
                );
            }
            key
        });
        let sounds = match self.required(top, "sounds", "") {
            Some(v) => self.sounds(v),
            None => Vec::new(),
        };
        let patterns = match self.required(top, "patterns", "") {
            Some(v) => self.patterns(v, &sounds),
            None => Vec::new(),
        };
        let sections = match self.required(top, "sections", "") {
            Some(v) => self.sections(v, &sounds, &patterns),
            None => Vec::new(),
        };
        let mix = self.mix(top.get("mix"), &sounds);
        if !self.errors.is_empty() {
            return None;
        }
        Some(Score {
            bpm: bpm?,
            swing: swing?,
            seed: seed? as u32,
            key: key?,
            sounds: sounds
                .into_iter()
                .map(|d| {
                    Some(Sound {
                        name: d.name,
                        instrument: d.instrument?,
                        params: d.params,
                        swing: d.swing,
                        library: d.library,
                    })
                })
                .collect::<Option<_>>()?,
            patterns: patterns
                .into_iter()
                .map(|d| {
                    Some(Pattern {
                        name: d.name,
                        bars: d.bars?,
                        rows: d.rows,
                    })
                })
                .collect::<Option<_>>()?,
            sections,
            mix,
        })
    }

    fn sounds(&mut self, v: &Value) -> Vec<SoundDraft> {
        let Some(map) = self.map(v, "sounds") else {
            return Vec::new();
        };
        if map.len() > MAX_SOUNDS {
            self.err(
                "sounds",
                format!("{} sounds, at most {MAX_SOUNDS}", map.len()),
            );
        }
        let mut out = Vec::new();
        for (name, v) in sorted(map) {
            let path = join("sounds", name);
            if !good_name(name) {
                self.err(&path, "a name takes 1 to 24 of a-z, 0-9 and _");
                continue;
            }
            out.push(self.sound(name, v, &path));
        }
        out
    }

    fn sound(&mut self, name: &str, v: &Value, path: &str) -> SoundDraft {
        let mut draft = SoundDraft {
            name: name.to_string(),
            instrument: None,
            params: Vec::new(),
            swing: None,
            library: None,
        };
        let Some(map) = self.object(v, path, &["instrument", "params", "swing", "library"]) else {
            return draft;
        };
        if let Some(v) = self.required(map, "instrument", path) {
            let ipath = join(path, "instrument");
            if let Some(s) = self.string(v, &ipath) {
                draft.instrument = instrument(s);
                if draft.instrument.is_none() {
                    self.err(&ipath, format!("no instrument {s}"));
                }
            }
        }
        if let Some(v) = map.get("swing") {
            draft.swing = self.number(v, &join(path, "swing"), SWING.0, SWING.1);
        }
        if let Some(v) = map.get("library") {
            let lpath = join(path, "library");
            if let Some(s) = self.string(v, &lpath) {
                match draft.instrument {
                    Some(i) if i.name != "sampler" => self.err(
                        &lpath,
                        format!("only a sampler takes a library, not {}", i.name),
                    ),
                    _ if !good_library(s) => {
                        self.err(&lpath, "a library name takes 1 to 48 of a-z, 0-9, _ and -")
                    }
                    _ => draft.library = Some(s.to_string()),
                }
            }
        }
        let Some(inst) = draft.instrument else {
            return draft;
        };
        draft.params = inst.params.iter().map(|p| p.default).collect();
        if let Some(v) = map.get("params") {
            let ppath = join(path, "params");
            if let Some(params) = self.map(v, &ppath) {
                for (pname, pv) in sorted(params) {
                    let path = join(&ppath, pname);
                    let spec = inst.params.iter().find(|p| p.name == pname.as_str());
                    let value = match (pv, spec) {
                        (Value::String(s), Some(spec)) if !spec.choices.is_empty() => {
                            match spec.choices.iter().position(|c| c == s) {
                                Some(i) => i as f32,
                                None => {
                                    self.err(
                                        &path,
                                        format!(
                                            "{} has no choice {s}; it takes {}",
                                            inst.name,
                                            spec.choices.join(", ")
                                        ),
                                    );
                                    continue;
                                }
                            }
                        }
                        (Value::Number(n), _) => n.as_f64().unwrap_or(f64::NAN) as f32,
                        (_, Some(_)) => {
                            self.err(&path, "expected a number");
                            continue;
                        }
                        (_, None) => f32::NAN,
                    };
                    match check(inst.name, inst.params, pname, value) {
                        Ok(i) => draft.params[i] = value,
                        Err(e) => self.err(&path, e.to_string()),
                    }
                }
            }
        }
        draft
    }

    fn patterns(&mut self, v: &Value, sounds: &[SoundDraft]) -> Vec<PatternDraft> {
        let Some(map) = self.map(v, "patterns") else {
            return Vec::new();
        };
        if map.len() > MAX_PATTERNS {
            self.err(
                "patterns",
                format!("{} patterns, at most {MAX_PATTERNS}", map.len()),
            );
        }
        let mut out = Vec::new();
        for (name, v) in sorted(map) {
            let path = join("patterns", name);
            if !good_name(name) {
                self.err(&path, "a name takes 1 to 24 of a-z, 0-9 and _");
                continue;
            }
            let mut draft = PatternDraft {
                name: name.clone(),
                bars: None,
                rows: Vec::new(),
            };
            if let Some(map) = self.object(v, &path, &["bars", "rows"]) {
                if let Some(b) = self.required(map, "bars", &path) {
                    let bpath = join(&path, "bars");
                    if let Some(n) = self.whole(b, &bpath, 1, 8) {
                        if PATTERN_BARS.contains(&(n as u32)) {
                            draft.bars = Some(n as u32);
                        } else {
                            self.err(&bpath, format!("{n} bars; a pattern takes 1, 2, 4 or 8"));
                        }
                    }
                }
                if let Some(r) = self.required(map, "rows", &path) {
                    draft.rows = self.rows(r, &join(&path, "rows"), draft.bars, sounds);
                }
            }
            out.push(draft);
        }
        out
    }

    fn rows(
        &mut self,
        v: &Value,
        path: &str,
        bars: Option<u32>,
        sounds: &[SoundDraft],
    ) -> Vec<(usize, Row)> {
        let Some(map) = self.map(v, path) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        for (name, v) in sorted(map) {
            let path = join(path, name);
            let Some(k) = sounds.iter().position(|s| s.name == *name) else {
                self.err(&path, format!("no sound {name}"));
                continue;
            };
            let is_notes = v.is_array();
            if let Some(inst) = sounds[k].instrument {
                match (inst.kind, is_notes) {
                    (Kind::Drum, true) => {
                        self.err(
                            &path,
                            format!(
                                "{name} is a drum sound ({}); it takes a step string",
                                inst.name
                            ),
                        );
                        continue;
                    }
                    (Kind::Note, false) => {
                        self.err(
                            &path,
                            format!(
                                "{name} is a note sound ({}); it takes a list of notes",
                                inst.name
                            ),
                        );
                        continue;
                    }
                    _ => {}
                }
            }
            let row = if is_notes {
                Row::Notes(self.notes(v, &path, bars))
            } else {
                Row::Drum(self.drum_row(v, &path, bars))
            };
            out.push((k, row));
        }
        out
    }

    fn drum_row(&mut self, v: &Value, path: &str, bars: Option<u32>) -> DrumRow {
        let mut row = DrumRow {
            steps: Vec::new(),
            velocity: None,
            pitch: None,
            decay: None,
            chance: None,
        };
        let need = bars.map(|b| (b, (b * STEPS_PER_BAR) as usize));
        let (text, spath, lanes) = match v {
            Value::String(s) => (Some(s.as_str()), path.to_string(), None),
            Value::Object(_) => {
                let Some(map) =
                    self.object(v, path, &["steps", "velocity", "pitch", "decay", "chance"])
                else {
                    return row;
                };
                let spath = join(path, "steps");
                let text = self
                    .required(map, "steps", path)
                    .and_then(|s| self.string(s, &spath));
                (text, spath, Some(map))
            }
            _ => {
                self.err(path, "expected a step string or an object with steps");
                return row;
            }
        };
        if let Some(text) = text {
            let mut bad = false;
            for c in text.chars() {
                let hit = match c {
                    '|' | ' ' => continue,
                    '.' => Hit::Rest,
                    'x' => Hit::Hit,
                    'X' => Hit::Accent,
                    'g' => Hit::Ghost,
                    '2' => Hit::Roll(2),
                    '3' => Hit::Roll(3),
                    '4' => Hit::Roll(4),
                    '6' => Hit::Roll(6),
                    _ => {
                        if !bad {
                            self.err(
                                &spath,
                                format!(
                                    "{c:?} at step {} is not a step; one of . x X g 2 3 4 6",
                                    row.steps.len()
                                ),
                            );
                        }
                        bad = true;
                        Hit::Rest
                    }
                };
                row.steps.push(hit);
            }
            if let Some((b, n)) = need {
                if !bad && row.steps.len() != n {
                    self.err(
                        &spath,
                        format!(
                            "{} steps, {} {b}-bar pattern needs {n}",
                            row.steps.len(),
                            article(b)
                        ),
                    );
                }
            }
        }
        if let Some(map) = lanes {
            row.velocity = self.lane(map, "velocity", path, need, 0.0, 1.0);
            row.pitch = self.lane(map, "pitch", path, need, -24.0, 24.0);
            row.decay = self.lane(map, "decay", path, need, 0.1, 4.0);
            row.chance = self.lane(map, "chance", path, need, 0.0, 1.0);
        }
        row
    }

    fn lane(
        &mut self,
        map: &Map<String, Value>,
        key: &str,
        path: &str,
        need: Option<(u32, usize)>,
        low: f64,
        high: f64,
    ) -> Option<Vec<f32>> {
        let v = map.get(key)?;
        let path = join(path, key);
        let items = self.array(v, &path)?;
        if let Some((b, n)) = need {
            if items.len() != n {
                self.err(
                    &path,
                    format!(
                        "{} values, {} {b}-bar pattern needs {n}",
                        items.len(),
                        article(b)
                    ),
                );
            }
        }
        let values: Vec<f32> = items
            .iter()
            .enumerate()
            .map(|(i, x)| self.number(x, &index(&path, i), low, high).unwrap_or(0.0) as f32)
            .collect();
        Some(values)
    }

    fn notes(&mut self, v: &Value, path: &str, bars: Option<u32>) -> Vec<Note> {
        let Some(items) = self.array(v, path) else {
            return Vec::new();
        };
        if items.len() > MAX_NOTES {
            self.err(path, format!("{} notes, at most {MAX_NOTES}", items.len()));
        }
        let end = bars.map(|b| (b * STEPS_PER_BAR) as u64);
        let mut out = Vec::new();
        for (i, item) in items.iter().enumerate() {
            let npath = index(path, i);
            let Some(map) = self.object(item, &npath, &["step", "len", "note", "vel", "passing"])
            else {
                continue;
            };
            let limit = end.unwrap_or(u64::from(u32::MAX));
            let step = self
                .required(map, "step", &npath)
                .and_then(|v| self.whole(v, &join(&npath, "step"), 0, limit.saturating_sub(1)));
            let len = self
                .required(map, "len", &npath)
                .and_then(|v| self.whole(v, &join(&npath, "len"), 1, limit));
            let note = self.required(map, "note", &npath).and_then(|v| {
                let path = join(&npath, "note");
                let s = self.string(v, &path)?;
                match note_number(s) {
                    None => {
                        self.err(
                            &path,
                            format!("{s:?} is not a note name like F#1, Bb2 or C-1"),
                        );
                        None
                    }
                    Some(n) if !(0..=127).contains(&n) => {
                        self.err(&path, format!("{s} is outside C-1..G9 (MIDI 0..127)"));
                        None
                    }
                    Some(n) => Some(n as u8),
                }
            });
            let vel = self
                .required(map, "vel", &npath)
                .and_then(|v| self.number(v, &join(&npath, "vel"), 0.0, 1.0));
            let passing = match map.get("passing") {
                None => false,
                Some(Value::Bool(b)) => *b,
                Some(_) => {
                    self.err(&join(&npath, "passing"), "expected true or false");
                    false
                }
            };
            if let (Some(step), Some(len), Some(end)) = (step, len, end) {
                if step + len > end {
                    self.err(
                        &npath,
                        format!("runs to step {}, past the pattern end at {end}", step + len),
                    );
                }
            }
            if let (Some(step), Some(len), Some(note), Some(vel)) = (step, len, note, vel) {
                out.push(Note {
                    step: step as u32,
                    len: len as u32,
                    note,
                    vel: vel as f32,
                    passing,
                });
            }
        }
        out
    }

    fn sections(
        &mut self,
        v: &Value,
        sounds: &[SoundDraft],
        patterns: &[PatternDraft],
    ) -> Vec<Section> {
        let Some(items) = self.array(v, "sections") else {
            return Vec::new();
        };
        if items.is_empty() {
            self.err("sections", "no sections; a song needs at least one");
        }
        let mut out = Vec::new();
        let mut total: u64 = 0;
        for (i, item) in items.iter().enumerate() {
            let path = index("sections", i);
            let Some(map) = self.object(item, &path, &["name", "bars", "play", "mute", "fill"])
            else {
                continue;
            };
            let name = self.required(map, "name", &path).and_then(|v| {
                let npath = join(&path, "name");
                let s = self.string(v, &npath)?;
                if !good_section_name(s) {
                    self.err(
                        &npath,
                        "a section name takes 1 to 48 of A-Z, a-z, 0-9, space, _ and -, not \
                         starting with a space",
                    );
                }
                Some(s.to_string())
            });
            let bpath = join(&path, "bars");
            let bars = self
                .required(map, "bars", &path)
                .and_then(|v| self.whole(v, &bpath, 1, u64::from(MAX_BARS)))
                .map(|b| b as u32);
            total += u64::from(bars.unwrap_or(0));
            let mut play = Vec::new();
            if let Some(v) = self.required(map, "play", &path) {
                let ppath = join(&path, "play");
                if let Some(names) = self.array(v, &ppath) {
                    for (j, n) in names.iter().enumerate() {
                        let jpath = index(&ppath, j);
                        let Some(k) = self.pattern_ref(n, &jpath, patterns) else {
                            continue;
                        };
                        if play.contains(&k) {
                            self.err(&jpath, format!("{} is played twice", patterns[k].name));
                            continue;
                        }
                        play.push(k);
                    }
                }
            }
            // the fill is exempt: it replaces the played rows on the last bar
            for (j, &a) in play.iter().enumerate() {
                for &b in &play[j + 1..] {
                    let shared = patterns[a]
                        .rows
                        .iter()
                        .find(|(k, _)| patterns[b].rows.iter().any(|(m, _)| m == k));
                    if let Some((k, _)) = shared {
                        self.err(
                            &join(&path, "play"),
                            format!(
                                "patterns {} and {} both have rows for sound {}",
                                patterns[a].name, patterns[b].name, sounds[*k].name
                            ),
                        );
                    }
                }
            }
            if let Some(b) = bars {
                for &k in &play {
                    if let Some(pb) = patterns[k].bars {
                        if b % pb != 0 {
                            self.err(
                                &bpath,
                                format!(
                                    "{b} bars is not a multiple of pattern {}'s {pb}",
                                    patterns[k].name
                                ),
                            );
                        }
                    }
                }
            }
            let mut mute = Vec::new();
            if let Some(v) = map.get("mute") {
                let mpath = join(&path, "mute");
                if let Some(names) = self.array(v, &mpath) {
                    for (j, n) in names.iter().enumerate() {
                        let jpath = index(&mpath, j);
                        let Some(s) = self.string(n, &jpath) else {
                            continue;
                        };
                        match sounds.iter().position(|d| d.name == s) {
                            Some(k) if !mute.contains(&k) => mute.push(k),
                            Some(_) => {}
                            None => self.err(&jpath, format!("no sound {s}")),
                        }
                    }
                }
            }
            let mut fill = None;
            if let Some(v) = map.get("fill") {
                let fpath = join(&path, "fill");
                if let Some(k) = self.pattern_ref(v, &fpath, patterns) {
                    match patterns[k].bars {
                        Some(b) if b != 1 => self.err(
                            &fpath,
                            format!("pattern {} has {b} bars; a fill takes 1", patterns[k].name),
                        ),
                        _ => fill = Some(k),
                    }
                }
            }
            if let (Some(name), Some(bars)) = (name, bars) {
                out.push(Section {
                    name,
                    bars,
                    play,
                    mute,
                    fill,
                });
            }
        }
        if total > u64::from(MAX_BARS) {
            self.err(
                "sections",
                format!("{total} bars in all, at most {MAX_BARS}"),
            );
        }
        out
    }

    fn pattern_ref(&mut self, v: &Value, path: &str, patterns: &[PatternDraft]) -> Option<usize> {
        let s = self.string(v, path)?;
        let k = patterns.iter().position(|p| p.name == s);
        if k.is_none() {
            self.err(path, format!("no pattern {s}"));
        }
        k
    }

    fn mix(&mut self, v: Option<&Value>, sounds: &[SoundDraft]) -> Mix {
        let empty = rack_text(&[]);
        let mut mix = Mix {
            sounds: sounds
                .iter()
                .map(|_| SoundMix {
                    gain_db: 0.0,
                    pan: 0.0,
                    rack: empty.clone(),
                    sidechain: None,
                })
                .collect(),
            master: empty,
        };
        let Some(v) = v else {
            return mix;
        };
        let Some(map) = self.object(v, "mix", &["sounds", "master"]) else {
            return mix;
        };
        if let Some(v) = map.get("sounds") {
            if let Some(entries) = self.map(v, "mix.sounds") {
                for (name, v) in sorted(entries) {
                    let path = join("mix.sounds", name);
                    let Some(k) = sounds.iter().position(|s| s.name == *name) else {
                        self.err(&path, format!("no sound {name}"));
                        continue;
                    };
                    let Some(m) = self.object(v, &path, &["gain_db", "pan", "rack", "sidechain"])
                    else {
                        continue;
                    };
                    if let Some(v) = m.get("gain_db") {
                        let g = self.number(v, &join(&path, "gain_db"), -60.0, 12.0);
                        mix.sounds[k].gain_db = g.unwrap_or(0.0) as f32;
                    }
                    if let Some(v) = m.get("pan") {
                        let p = self.number(v, &join(&path, "pan"), -1.0, 1.0);
                        mix.sounds[k].pan = p.unwrap_or(0.0) as f32;
                    }
                    if let Some(v) = m.get("rack") {
                        mix.sounds[k].rack = self.rack(v, &join(&path, "rack"));
                    }
                    if let Some(v) = m.get("sidechain") {
                        let spath = join(&path, "sidechain");
                        if let Some(s) = self.string(v, &spath) {
                            match sounds.iter().position(|d| d.name == s) {
                                Some(j) if j == k => {
                                    self.err(&spath, "a sound cannot be its own sidechain")
                                }
                                Some(j) => mix.sounds[k].sidechain = Some(j),
                                None => self.err(&spath, format!("no sound {s}")),
                            }
                        }
                    }
                }
            }
        }
        if let Some(v) = map.get("master") {
            mix.master = self.rack(v, "mix.master");
        }
        mix
    }

    /// The rack's JSON once every slot builds through [`Rack::from_json`] and none is
    /// offline-only; each slot is built alone so an error names its slot.
    fn rack(&mut self, v: &Value, path: &str) -> String {
        let Some(slots) = self.array(v, path) else {
            return rack_text(&[]);
        };
        if slots.len() > MAX_RACK {
            self.err(
                path,
                format!("{} effects, a rack takes at most {MAX_RACK}", slots.len()),
            );
        }
        for (i, slot) in slots.iter().enumerate() {
            let spath = index(path, i);
            match Rack::from_json(&rack_text(std::slice::from_ref(slot))) {
                Err(e) => self.err(&spath, e.to_string()),
                Ok(rack) => {
                    let name = rack.effect_name(0).unwrap_or_default();
                    if make(name).is_some_and(|e| e.offline_only()) {
                        self.err(
                            &spath,
                            format!("{name} works only offline; a beat plays in real time"),
                        );
                    }
                }
            }
        }
        rack_text(slots)
    }
}

fn rack_text(slots: &[Value]) -> String {
    json!({ "effects": slots }).to_string()
}
