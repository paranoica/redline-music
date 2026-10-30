//! The effect interface and the rack: effects in the user's order, configured by JSON.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
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

/// A stereo effect. `process` must not allocate, lock or wait: `reset` sizes what it needs.
pub trait Effect: Send {
    fn name(&self) -> &'static str;
    fn params(&self) -> &'static [ParamSpec];
    fn set(&mut self, name: &str, value: f32) -> Result<(), ParamError>;
    fn reset(&mut self, rate: f32);
    fn process(&mut self, block: &mut [[f32; 2]]);
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
    params: Map<String, Value>,
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
            for (name, value) in &slot.params {
                let value = param_value(effect.as_ref(), name, value)?;
                effect.set(name, value)?;
            }
            rack.effects.push(effect);
            rack.on.push(slot.on);
        }
        Ok(rack)
    }

    pub fn reset(&mut self, rate: f32) {
        for e in &mut self.effects {
            e.reset(rate);
        }
    }

    pub fn process(&mut self, block: &mut [[f32; 2]]) {
        for (e, &on) in self.effects.iter_mut().zip(&self.on) {
            if on {
                e.process(block);
            }
        }
    }

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
