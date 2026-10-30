//! What the JACK backend decides without JACK: its command line, the latency it asks
//! PipeWire for, the ports it connects, the beat it accepts, the frame counter and the
//! startup line.

use crate::budget::{monitor_budget, round_trip_estimate, MONITOR_SECONDS};
use crate::protocol::{RoundTripSource, Startup};
use crate::wav::Wav;
use std::path::PathBuf;

pub const USAGE: &str = "\
usage: redline-rec BEAT.wav [options]

BEAT.wav is a 32-bit float WAV at the audio graph's rate. Commands come on stdin and
replies go to stdout, one JSON object a line.

options:
  --mic PORT          the capture port the mic is on (default: the first physical one)
  --left PORT         the playback port of the left output (default: the first physical one)
  --right PORT        the playback port of the right output (default: the second)
  --no-connect        connect nothing; ports are left to a patchbay
  --round-trip N      the calibrated I/O round trip in frames (default: the ports' estimate)
  --quantum N         the period to ask PipeWire for, in frames (default: 64)
  --name NAME         the JACK client name (default: redline-rec)";

const MAX_QUANTUM: u32 = 8192;

#[derive(Clone, Debug, PartialEq)]
pub struct Args {
    pub beat: PathBuf,
    pub mic: Option<String>,
    pub left: Option<String>,
    pub right: Option<String>,
    pub connect: bool,
    /// Calibrated, in frames.
    pub round_trip: Option<u64>,
    pub quantum: u32,
    pub name: String,
}

/// The arguments after the program name.
pub fn parse_args(args: impl IntoIterator<Item = String>) -> Result<Args, String> {
    let mut beat = None;
    let mut parsed = Args {
        beat: PathBuf::new(),
        mic: None,
        left: None,
        right: None,
        connect: true,
        round_trip: None,
        quantum: 64,
        name: "redline-rec".into(),
    };
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        let mut value = || args.next().ok_or_else(|| format!("{arg} needs a value"));
        match arg.as_str() {
            "--mic" => parsed.mic = Some(value()?),
            "--left" => parsed.left = Some(value()?),
            "--right" => parsed.right = Some(value()?),
            "--name" => parsed.name = value()?,
            "--no-connect" => parsed.connect = false,
            "--round-trip" => {
                let v = value()?;
                parsed.round_trip = Some(
                    v.parse()
                        .map_err(|_| format!("--round-trip takes frames, not {v}"))?,
                );
            }
            "--quantum" => {
                let v = value()?;
                parsed.quantum = v
                    .parse()
                    .ok()
                    .filter(|q| (1..=MAX_QUANTUM).contains(q))
                    .ok_or_else(|| format!("--quantum takes 1 to {MAX_QUANTUM} frames, not {v}"))?;
            }
            flag if flag.starts_with("--") => return Err(format!("unknown option {flag}")),
            path => {
                if let Some(first) = &beat {
                    return Err(format!("one beat only: {first:?} and {path:?}"));
                }
                beat = Some(PathBuf::from(path));
            }
        }
    }
    parsed.beat = beat.ok_or("the beat WAV is missing")?;
    if !parsed.connect && (parsed.mic.is_some() || parsed.left.is_some() || parsed.right.is_some())
    {
        return Err("--no-connect connects nothing, so it takes no port names".into());
    }
    Ok(parsed)
}

/// The value for `PIPEWIRE_LATENCY` that asks for `frames` at `rate`, unless the user has
/// set it or `PIPEWIRE_QUANTUM` already.
pub fn latency_request(
    latency: Option<&str>,
    quantum: Option<&str>,
    frames: u32,
    rate: u32,
) -> Option<String> {
    let set = |v: Option<&str>| v.is_some_and(|v| !v.is_empty());
    if set(latency) || set(quantum) {
        return None;
    }
    Some(format!("{frames}/{rate}"))
}

/// JACK's 32-bit frame time as a counter that does not wrap: about a day at 48 kHz would.
#[derive(Clone, Copy, Debug, Default)]
pub struct FrameClock {
    last: Option<u32>,
    total: u64,
}

impl FrameClock {
    pub fn frame(&mut self, jack: u32) -> u64 {
        self.total = match self.last {
            Some(last) => self.total + jack.wrapping_sub(last) as u64,
            None => jack as u64,
        };
        self.last = Some(jack);
        self.total
    }
}

/// The ports the client's own are connected to; `None` is left unconnected.
#[derive(Clone, Debug, PartialEq)]
pub struct Connections {
    pub mic: Option<String>,
    pub left: Option<String>,
    pub right: Option<String>,
}

/// The ports named on the command line, the rest from the physical `captures` and
/// `playbacks`, in JACK's order.
pub fn pick_ports(
    args: &Args,
    captures: &[String],
    playbacks: &[String],
) -> Result<Connections, String> {
    if !args.connect {
        return Ok(Connections {
            mic: None,
            left: None,
            right: None,
        });
    }
    let mic = match &args.mic {
        Some(m) => m.clone(),
        None => captures
            .first()
            .cloned()
            .ok_or("no microphone: JACK has no physical capture port; name one with --mic")?,
    };
    let left = match &args.left {
        Some(p) => p.clone(),
        None => playbacks
            .first()
            .cloned()
            .ok_or("no audio output: JACK has no physical playback port; name one with --left")?,
    };
    // a single output plays both sides
    let right = args
        .right
        .clone()
        .or_else(|| playbacks.get(1).cloned())
        .unwrap_or_else(|| left.clone());
    Ok(Connections {
        mic: Some(mic),
        left: Some(left),
        right: Some(right),
    })
}

/// The beat as stereo frames, refused unless it is at the graph's rate: Python resamples
/// it before the start, and playing it at another rate would shift every take.
pub fn beat_frames(wav: &Wav, graph_rate: u32) -> Result<Vec<[f32; 2]>, String> {
    if wav.rate != graph_rate {
        return Err(format!(
            "the beat is at {} Hz and the audio graph runs at {graph_rate} Hz; resample the beat to {graph_rate} Hz",
            wav.rate
        ));
    }
    match wav.channels {
        1 => Ok(wav.samples.iter().map(|&v| [v, v]).collect()),
        2 => Ok(wav.samples.chunks_exact(2).map(|f| [f[0], f[1]]).collect()),
        n => Err(format!("the beat has {n} channels; one or two are played")),
    }
}

/// What the graph granted; latencies as (min, max) frames.
#[derive(Clone, Debug, PartialEq)]
pub struct Graph {
    pub rate: u32,
    pub quantum: u32,
    pub capture: (u32, u32),
    pub playback: (u32, u32),
}

/// The startup report: the round trip is the calibrated one when given, else the ports'
/// estimate, and the monitor budget follows from it.
pub fn startup(
    client: &str,
    graph: &Graph,
    requested_quantum: u32,
    latency_request: Option<String>,
    calibrated: Option<u64>,
    connections: Connections,
    beat_frames: u64,
) -> Startup {
    let (round_trip, round_trip_source) = match calibrated {
        Some(frames) => (frames, RoundTripSource::Calibrated),
        None => (
            round_trip_estimate(graph.capture, graph.playback),
            RoundTripSource::PortLatencyEstimate,
        ),
    };
    let whole = (MONITOR_SECONDS * graph.rate as f64).round() as u64;
    Startup {
        client: client.into(),
        rate: graph.rate,
        quantum: graph.quantum,
        requested_quantum,
        latency_request,
        capture_latency: [graph.capture.0, graph.capture.1],
        playback_latency: [graph.playback.0, graph.playback.1],
        round_trip,
        round_trip_ms: round_trip as f64 * 1000.0 / graph.rate as f64,
        round_trip_source,
        under_10ms: round_trip < whole,
        monitor_budget: monitor_budget(graph.rate, round_trip),
        mic: connections.mic,
        left: connections.left,
        right: connections.right,
        beat_frames,
    }
}
