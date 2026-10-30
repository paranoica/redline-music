//! The control protocol: one JSON object a line each way. A command names itself in "cmd",
//! a reply names the command it answers in "reply", or is `{"reply": "error", "error": ...}`.
//!
//! - `{"cmd": "start", "path": "...", "offset": N}`: record a take to a new file; `offset` is
//!   the hardware round trip in frames, so the take's frame 0 is the beat's frame 0.
//! - `{"cmd": "stop"}`: finish the take; the reply carries its [`TakeReport`].
//! - `{"cmd": "set_rack", "rack": {...}, "budget": N}`: a new monitor rack, its effects
//!   switched off from the end until its latency fits `budget` frames, when given.
//! - `{"cmd": "meters"}`: peaks since the last call, xruns, overflows, frames since the take
//!   started (the round trip included), the monitor rack's latency.
//! - `{"cmd": "quit"}`.

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;

#[derive(Clone, Debug, PartialEq, Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    Start {
        path: PathBuf,
        offset: u64,
    },
    Stop,
    SetRack {
        rack: Value,
        #[serde(default)]
        budget: Option<usize>,
    },
    Meters,
    Quit,
}

pub fn parse(line: &str) -> Result<Command, String> {
    let command: Command = serde_json::from_str(line).map_err(|e| format!("{e}: {line}"))?;
    // deny_unknown_fields does not reach the variants without fields
    if matches!(command, Command::Stop | Command::Meters | Command::Quit) {
        let value: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
        if let Some(extra) = value
            .as_object()
            .and_then(|o| o.keys().find(|k| *k != "cmd"))
        {
            return Err(format!("unknown field `{extra}`: {line}"));
        }
    }
    Ok(command)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Cause {
    /// The backend reported an xrun.
    Xrun,
    /// The frame counter skipped frames.
    CounterGap,
    /// The writer fell behind and the ring was full.
    Overflow,
}

/// Silence written into a take where the input was lost: take frames `start..start + frames`.
/// An xrun that lost nothing is logged with no frames.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Dropout {
    pub start: u64,
    pub frames: u64,
    pub causes: Vec<Cause>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct TakeReport {
    pub path: PathBuf,
    pub rate: u32,
    pub frames: u64,
    pub offset: u64,
    pub dropouts: Vec<Dropout>,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct Meters {
    pub mic_peak: f32,
    pub out_peak: f32,
    pub xruns: u64,
    pub overflows: u64,
    pub recording: bool,
    pub take_frames: u64,
    pub rack_latency: usize,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "reply", rename_all = "snake_case")]
pub enum Reply {
    Start {
        path: PathBuf,
    },
    Stop {
        take: TakeReport,
    },
    SetRack {
        latency: usize,
        dropped: Vec<String>,
    },
    Meters(Meters),
    Quit,
    Error {
        error: String,
    },
}

/// The reply as one line, without the newline.
pub fn format(reply: &Reply) -> String {
    serde_json::to_string(reply).expect("replies serialise")
}
