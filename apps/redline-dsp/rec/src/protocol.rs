//! The control protocol: one JSON object a line each way. A command names itself in "cmd",
//! a reply names the command it answers in "reply", or is `{"reply": "error", "error": ...}`.
//!
//! - `{"cmd": "start", "path": "...", "offset": N}`: record a take to a new file; `offset` is
//!   the hardware round trip in frames, so the take's frame 0 is the beat's frame 0.
//! - `{"cmd": "stop"}`: finish the take; the reply carries its [`TakeReport`].
//! - `{"cmd": "set_rack", "rack": {...}, "budget": N}`: a new monitor rack, its effects
//!   switched off from the end until its latency fits `budget` frames, or the backend's
//!   default budget when none is given.
//! - `{"cmd": "meters"}`: peaks since the last call, xruns, overflows, frames since the take
//!   started (the round trip included), the monitor rack's latency.
//! - `{"cmd": "quit"}`.
//!
//! Before any reply the backend writes one [`Startup`] line, `{"event": "started", ...}`.
//! When the beat is not at the graph's rate it writes `{"event": "wrong_rate", "rate": N,
//! "beat_rate": N}` instead and exits, so the beat can be resampled to `rate`.

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

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RoundTripSource {
    /// Measured through the singer's path and passed in.
    Calibrated,
    /// The ports' latency ranges: what the drivers claim, not a measurement.
    PortLatencyEstimate,
}

/// What the backend got from the audio graph; latencies in frames.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Startup {
    pub client: String,
    pub rate: u32,
    /// The period the graph granted.
    pub quantum: u32,
    pub requested_quantum: u32,
    /// The latency asked of PipeWire, when the backend set it.
    pub latency_request: Option<String>,
    /// Min and max, as the mic port reports it.
    pub capture_latency: [u32; 2],
    /// Min and max, as the output ports report it.
    pub playback_latency: [u32; 2],
    pub round_trip: u64,
    pub round_trip_ms: f64,
    pub round_trip_source: RoundTripSource,
    pub under_10ms: bool,
    /// The latency a monitor rack may add; a `set_rack` without a budget is held to it.
    pub monitor_budget: usize,
    pub mic: Option<String>,
    pub left: Option<String>,
    pub right: Option<String>,
    pub beat_frames: u64,
}

#[derive(Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
enum Event<'a> {
    Started(&'a Startup),
    WrongRate { rate: u32, beat_rate: u32 },
}

/// The startup line, without the newline.
pub fn format_startup(startup: &Startup) -> String {
    serde_json::to_string(&Event::Started(startup)).expect("the startup line serialises")
}

/// The line refusing a beat at `beat_rate` on a graph at `rate`, without the newline.
pub fn format_wrong_rate(rate: u32, beat_rate: u32) -> String {
    serde_json::to_string(&Event::WrongRate { rate, beat_rate })
        .expect("the wrong rate line serialises")
}
