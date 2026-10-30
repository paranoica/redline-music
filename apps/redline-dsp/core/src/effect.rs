//! The effect interface and the rack: effects in the user's order, configured by JSON.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fmt;

pub use crate::effects::{make, EFFECTS};

/// A param's range and default. A param with choices takes the index of one; JSON may name it.
#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct ParamSpec {
    pub name: &'static str,
    pub low: f32,
    pub high: f32,
    pub default: f32,
    pub unit: &'static str,
    #[serde(skip_serializing_if = "<[_]>::is_empty")]
    pub choices: &'static [&'static str],
}

impl ParamSpec {
    pub const fn range(
        name: &'static str,
        low: f32,
        high: f32,
        default: f32,
        unit: &'static str,
    ) -> Self {
        ParamSpec {
            name,
            low,
            high,
            default,
            unit,
            choices: &[],
        }
    }

    pub const fn choice(name: &'static str, choices: &'static [&'static str]) -> Self {
        ParamSpec {
            name,
            low: 0.0,
            high: (choices.len() - 1) as f32,
            default: 0.0,
            unit: "choice",
            choices,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum ParamError {
    Unknown {
        effect: &'static str,
        param: String,
    },
    OutOfRange {
        effect: &'static str,
        param: &'static str,
        value: f32,
        low: f32,
        high: f32,
    },
}

impl fmt::Display for ParamError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParamError::Unknown { effect, param } => write!(f, "{effect} has no param {param}"),
            ParamError::OutOfRange {
                effect,
                param,
                value,
                low,
                high,
            } => write!(f, "{effect}.{param} = {value} is outside {low}..{high}"),
        }
    }
}

impl std::error::Error for ParamError {}

/// The index of the named param, once the value is inside its range.
pub fn check(
    effect: &'static str,
    specs: &'static [ParamSpec],
    name: &str,
    value: f32,
) -> Result<usize, ParamError> {
    let i = specs
        .iter()
        .position(|s| s.name == name)
        .ok_or_else(|| ParamError::Unknown {
            effect,
            param: name.to_string(),
        })?;
    let s = &specs[i];
    if !(s.low <= value && value <= s.high) {
        return Err(ParamError::OutOfRange {
            effect,
            param: s.name,
            value,
            low: s.low,
            high: s.high,
        });
    }
    Ok(i)
}

/// A stereo effect. `process` and `set` must not allocate, lock or wait: `reset` sizes what
/// it needs. The host runs `process` with subnormals flushed to zero
/// ([`crate::filters::flush_tiny`]), as the Python binding does; effects do not guard every
/// sample against them. Input may hold NaN or infinity: an effect that keeps state treats them
/// as silence, so one bad sample cannot poison it.
pub trait Effect: Send {
    fn name(&self) -> &'static str;
    fn params(&self) -> &'static [ParamSpec];
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError>;
    fn reset(&mut self, rate: f32);
    fn process(&mut self, block: &mut [[f32; 2]]);
    /// The side signal of the next `process` call, frame for frame, up to [`MAX_BLOCK`]
    /// frames. A block that comes without one, or the frames past its end, have a silent
    /// side. An effect with no side input ignores it.
    fn sidechain(&mut self, _side: &[[f32; 2]]) {}
    fn latency(&self) -> usize {
        0
    }
    fn offline_only(&self) -> bool {
        false
    }
}

/// Every effect with its params, as JSON: `[{"effect", "offline_only", "params": [...]}]`.
pub fn effects_json() -> String {
    #[derive(Serialize)]
    struct Entry {
        effect: &'static str,
        offline_only: bool,
        params: &'static [ParamSpec],
    }
    let entries: Vec<Entry> = EFFECTS
        .iter()
        .filter_map(|name| make(name))
        .map(|e| Entry {
            effect: e.name(),
            offline_only: e.offline_only(),
            params: e.params(),
        })
        .collect();
    serde_json::to_string(&entries).expect("specs serialise")
}

#[derive(Clone, Debug, PartialEq)]
pub struct RackError(pub String);

impl fmt::Display for RackError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for RackError {}

impl From<ParamError> for RackError {
    fn from(e: ParamError) -> Self {
        RackError(e.to_string())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RackSpec {
    effects: Vec<SlotSpec>,
}

fn on() -> bool {
    true
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SlotSpec {
    effect: String,
    #[serde(default = "on")]
    on: bool,
    #[serde(default)]
    params: Params,
}

/// A slot's params in their order; a JSON map would silently keep the last of a repeated key.
#[derive(Default)]
struct Params(Vec<(String, Value)>);

impl<'de> Deserialize<'de> for Params {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct Visitor;
        impl<'de> serde::de::Visitor<'de> for Visitor {
            type Value = Params;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("a map of params")
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(
                self,
                mut map: A,
            ) -> Result<Params, A::Error> {
                let mut params: Vec<(String, Value)> = Vec::new();
                while let Some((name, value)) = map.next_entry::<String, Value>()? {
                    if params.iter().any(|(n, _)| *n == name) {
                        return Err(serde::de::Error::custom(format!(
                            "param {name} is given twice"
                        )));
                    }
                    params.push((name, value));
                }
                Ok(Params(params))
            }
        }
        d.deserialize_map(Visitor)
    }
}

/// The lowest rate the rack runs at; the pitch effects' stretcher needs blocks of some length.
pub const MIN_RATE: f32 = 8000.0;

/// The longest block a side signal is handed over with; [`Rack::process_with`] splits longer
/// blocks.
pub const MAX_BLOCK: usize = 4096;

/// Non-finite samples replaced by silence.
pub fn finite(block: &mut [[f32; 2]]) {
    for v in block.as_flattened_mut() {
        if !v.is_finite() {
            *v = 0.0;
        }
    }
}

/// The time constant of a smoothed param, in seconds: short enough not to be heard as a lag
/// behind a knob, long enough that a jump across the whole range does not click.
pub(crate) const SMOOTHING: f32 = 0.02;

/// A param that glides to a new value along a one-pole curve instead of jumping.
#[derive(Clone, Copy, Debug)]
pub(crate) struct Smoothed {
    value: f32,
    target: f32,
    step: f32,
}

impl Smoothed {
    pub(crate) fn new(value: f32) -> Self {
        Smoothed {
            value,
            target: value,
            step: Self::step_for(48000.0),
        }
    }

    fn step_for(rate: f32) -> f32 {
        1.0 - (-1.0 / (SMOOTHING * rate)).exp()
    }

    pub(crate) fn set(&mut self, target: f32) {
        self.target = target;
    }

    /// At the target from the first sample on: a reset starts where the params are.
    pub(crate) fn reset(&mut self, rate: f32) {
        self.step = Self::step_for(rate);
        self.value = self.target;
    }

    pub(crate) fn settled(&self) -> bool {
        self.value == self.target
    }

    pub(crate) fn value(&self) -> f32 {
        self.value
    }

    /// The value for the next sample.
    #[inline]
    pub(crate) fn next(&mut self) -> f32 {
        if self.value != self.target {
            let gap = self.target - self.value;
            // close enough to land on the target exactly, so a settled param costs nothing
            self.value = if gap.abs() < 1e-6 {
                self.target
            } else {
                self.value + gap * self.step
            };
        }
        self.value
    }

    /// `n` samples on at once.
    pub(crate) fn skip(&mut self, n: usize) {
        if self.value != self.target {
            let gap = self.target - self.value;
            let left = gap * (1.0 - self.step).powi(n as i32);
            self.value = if left.abs() < 1e-6 {
                self.target
            } else {
                self.target - left
            };
        }
    }
}

/// Effects in order; one turned off is kept but skipped.
pub struct Rack {
    effects: Vec<Box<dyn Effect>>,
    on: Vec<bool>,
}

impl Rack {
    /// `{"effects": [{"effect": "gain", "on": true, "params": {"db": -3}}]}`; "on" defaults to
    /// true and a param left out to its default.
    pub fn from_json(text: &str) -> Result<Rack, RackError> {
        let spec: RackSpec =
            serde_json::from_str(text).map_err(|e| RackError(format!("rack: {e}")))?;
        let mut rack = Rack {
            effects: Vec::with_capacity(spec.effects.len()),
            on: Vec::with_capacity(spec.effects.len()),
        };
        for slot in spec.effects {
            let mut effect = make(&slot.effect)
                .ok_or_else(|| RackError(format!("no effect {}", slot.effect)))?;
            for (name, value) in &slot.params.0 {
                let value = param_value(effect.as_ref(), name, value)?;
                effect.set(name, value)?;
            }
            rack.effects.push(effect);
            rack.on.push(slot.on);
        }
        Ok(rack)
    }

    /// Sizes every effect for `rate`; a rate under [`MIN_RATE`] (or not a number) is refused
    /// and the effects are left as they were.
    pub fn reset(&mut self, rate: f32) -> Result<(), RackError> {
        // written so NaN fails it too
        if !(rate >= MIN_RATE && rate.is_finite()) {
            return Err(RackError(format!(
                "the rate {rate} Hz is under the rack's lowest, {MIN_RATE} Hz"
            )));
        }
        for e in &mut self.effects {
            e.reset(rate);
        }
        Ok(())
    }

    pub fn process(&mut self, block: &mut [[f32; 2]]) {
        finite(block);
        for (e, &on) in self.effects.iter_mut().zip(&self.on) {
            if on {
                e.process(block);
            }
        }
    }

    /// As `process`, with a side signal for the effects that listen to one (a sidechain
    /// compressor, a ducker, a vocoder's carrier). Frames of `side` past its end are silent.
    pub fn process_with(&mut self, block: &mut [[f32; 2]], side: &[[f32; 2]]) {
        finite(block);
        for (k, chunk) in block.chunks_mut(MAX_BLOCK).enumerate() {
            let start = (k * MAX_BLOCK).min(side.len());
            let part = &side[start..(start + chunk.len()).min(side.len())];
            for (e, &on) in self.effects.iter_mut().zip(&self.on) {
                if on {
                    e.sidechain(part);
                    e.process(chunk);
                }
            }
        }
    }

    /// Frames from an input to the output it shows up in: the sum over the effects switched
    /// on. An offline render feeds this many frames of silence after the input and drops as
    /// many from the front.
    pub fn latency(&self) -> usize {
        self.effects
            .iter()
            .zip(&self.on)
            .filter(|(_, &on)| on)
            .map(|(e, _)| e.latency())
            .sum()
    }

    pub fn len(&self) -> usize {
        self.effects.len()
    }

    pub fn is_empty(&self) -> bool {
        self.effects.is_empty()
    }
}

fn param_value(effect: &dyn Effect, name: &str, value: &Value) -> Result<f32, RackError> {
    let what = || format!("{}.{name}", effect.name());
    match value {
        Value::Number(n) => n
            .as_f64()
            .map(|v| v as f32)
            .ok_or_else(|| RackError(format!("{} is not a number", what()))),
        Value::String(s) => {
            let spec = effect.params().iter().find(|p| p.name == name);
            let choices = spec.map_or(&[][..], |p| p.choices);
            match choices.iter().position(|c| c == s) {
                Some(i) => Ok(i as f32),
                None if spec.is_some() && !choices.is_empty() => Err(RackError(format!(
                    "{} has no choice {s}; it takes {}",
                    what(),
                    choices.join(", ")
                ))),
                None if spec.is_some() => Err(RackError(format!("{} takes a number", what()))),
                None => Err(RackError(format!("{} has no param {name}", effect.name()))),
            }
        }
        _ => Err(RackError(format!("{} takes a number", what()))),
    }
}
