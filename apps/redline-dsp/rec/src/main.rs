//! The JACK backend: one client with a mic in and two outputs, so capture and playback run
//! on one callback and one frame counter. Built only with the `jack` feature.

use jack::{
    AudioIn, AudioOut, Client, ClientOptions, Control as Flow, LatencyType, LoggerType,
    NotificationHandler, Port, PortFlags, PortSpec, ProcessHandler, ProcessScope,
};
use redline_dsp_core::filters::flush_tiny;
use redline_rec::backend::{
    beat_frames, check_rate, latency_request, parse_args, pick_ports, startup, Args, FrameClock,
    Graph, USAGE,
};
use redline_rec::engine::{engine, serve, Config, Engine, XrunReporter};
use redline_rec::protocol::{format_startup, format_wrong_rate};
use redline_rec::wav;
use std::ffi::{c_char, CStr};
use std::io::{self, Write};
use std::process::exit;
use std::time::Duration;

/// Exit status when what the engine needs (a server, a mic, a beat at the right rate) is not
/// there.
const UNAVAILABLE: i32 = 2;
/// Exit status when the server goes away while running.
const SERVER_GONE: i32 = 3;

struct Process {
    engine: Engine,
    mic: Port<AudioIn>,
    out_l: Port<AudioOut>,
    out_r: Port<AudioOut>,
    clock: FrameClock,
}

impl ProcessHandler for Process {
    fn process(&mut self, _: &Client, ps: &ProcessScope) -> Flow {
        let frame = self.clock.frame(ps.last_frame_time());
        let mic = self.mic.as_slice(ps);
        let l = self.out_l.as_mut_slice(ps);
        let r = self.out_r.as_mut_slice(ps);
        self.engine.cycle(mic, l, r, frame);
        Flow::Continue
    }
}

struct Notifications {
    xruns: XrunReporter,
    rate: u32,
}

impl NotificationHandler for Notifications {
    fn thread_init(&self, _: &Client) {
        // left on for the thread's life; each cycle sets it again anyway
        std::mem::forget(flush_tiny());
    }

    fn xrun(&mut self, _: &Client) -> Flow {
        self.xruns.report();
        Flow::Continue
    }

    fn sample_rate(&mut self, _: &Client, rate: jack::Frames) -> Flow {
        if rate != self.rate {
            eprintln!(
                "redline-rec: the graph moved from {} Hz to {rate} Hz; the beat no longer fits",
                self.rate
            );
        }
        Flow::Continue
    }

    unsafe fn shutdown(&mut self, _: jack::ClientStatus, reason: &str) {
        eprintln!("redline-rec: the JACK server went away: {reason}");
        exit(SERVER_GONE);
    }
}

// JACK's default logger writes to stdout, which carries the protocol
unsafe extern "C" fn to_stderr(msg: *const c_char) {
    if !msg.is_null() {
        eprintln!("jack: {}", CStr::from_ptr(msg).to_string_lossy());
    }
}

fn latency(client: &Client, port: &str, mode: LatencyType) -> (u32, u32) {
    client
        .port_by_name(port)
        .map_or((0, 0), |p| p.get_latency_range(mode))
}

/// Tells the driver on stdout which rate to resample the beat to, then refuses it.
fn refuse_rate(beat_rate: u32, graph_rate: u32) -> Result<(), String> {
    let refused = check_rate(beat_rate, graph_rate);
    if refused.is_err() {
        let mut out = io::stdout().lock();
        let _ = writeln!(out, "{}", format_wrong_rate(graph_rate, beat_rate));
        let _ = out.flush();
    }
    refused
}

fn run(args: Args) -> Result<(), String> {
    let beat = wav::read(&args.beat)
        .map_err(|e| format!("cannot read the beat {}: {e}", args.beat.display()))?;
    let request = latency_request(
        std::env::var("PIPEWIRE_LATENCY").ok().as_deref(),
        std::env::var("PIPEWIRE_QUANTUM").ok().as_deref(),
        args.quantum,
        beat.rate,
    );
    // PipeWire reads it when the client opens; no other thread runs yet
    if let Some(r) = &request {
        std::env::set_var("PIPEWIRE_LATENCY", r);
    }
    jack::jack_sys::library()
        .map_err(|e| format!("no JACK library ({e}); install pipewire-jack or JACK"))?;
    jack::set_logger(LoggerType::Custom {
        info: to_stderr,
        error: to_stderr,
    });
    let (client, _) = Client::new(&args.name, ClientOptions::NO_START_SERVER).map_err(|e| {
        format!("no JACK server ({e}); is PipeWire with pipewire-jack, or JACK, running?")
    })?;
    let rate = client.sample_rate();
    // JACK proper knows its rate at open; pipewire-jack is caught after activation
    refuse_rate(beat.rate, rate)?;
    let frames = beat_frames(&beat, rate)?;
    let beat_len = frames.len() as u64;

    let register = |name: &str| format!("cannot register the port {name}");
    let mic = client
        .register_port("mic", AudioIn::default())
        .map_err(|_| register("mic"))?;
    let out_l = client
        .register_port("out_l", AudioOut::default())
        .map_err(|_| register("out_l"))?;
    let out_r = client
        .register_port("out_r", AudioOut::default())
        .map_err(|_| register("out_r"))?;
    let named = |r: Result<String, jack::Error>| r.map_err(|e| e.to_string());
    let (mic_name, l_name, r_name) = (
        named(mic.name())?,
        named(out_l.name())?,
        named(out_r.name())?,
    );

    let (engine, mut control) = engine(Config::new(rate, frames))?;
    let notifications = Notifications {
        xruns: engine.xrun_reporter(),
        rate,
    };
    let process = Process {
        engine,
        mic,
        out_l,
        out_r,
        clock: FrameClock::default(),
    };
    let active = client
        .activate_async(notifications, process)
        .map_err(|e| format!("the JACK client did not start: {e}"))?;
    let c = active.as_client();
    // pipewire-jack reports the rate asked for at open, which the graph may not run at
    std::thread::sleep(Duration::from_millis(200));
    refuse_rate(beat.rate, c.sample_rate())?;

    let spec = AudioIn::default();
    let audio = spec.jack_port_type();
    let captures = c.ports(
        None,
        Some(audio),
        PortFlags::IS_PHYSICAL | PortFlags::IS_OUTPUT,
    );
    let playbacks = c.ports(
        None,
        Some(audio),
        PortFlags::IS_PHYSICAL | PortFlags::IS_INPUT,
    );
    let connections = pick_ports(&args, &captures, &playbacks)?;
    let pairs = [
        (connections.mic.as_deref(), mic_name.as_str()),
        (
            Some(l_name.as_str()),
            connections.left.as_deref().unwrap_or(""),
        ),
        (
            Some(r_name.as_str()),
            connections.right.as_deref().unwrap_or(""),
        ),
    ];
    for (from, to) in pairs {
        if let (Some(from), false) = (from, to.is_empty()) {
            c.connect_ports_by_name(from, to)
                .map_err(|e| format!("cannot connect {from} to {to}: {e}"))?;
        }
    }
    // the latency ranges settle once the graph has taken the new links
    std::thread::sleep(Duration::from_millis(200));
    let capture = latency(c, &mic_name, LatencyType::Capture);
    let (pl, pr) = (
        latency(c, &l_name, LatencyType::Playback),
        latency(c, &r_name, LatencyType::Playback),
    );
    let graph = Graph {
        rate,
        quantum: c.buffer_size(),
        capture,
        playback: (pl.0.max(pr.0), pl.1.max(pr.1)),
    };
    let report = startup(
        c.name(),
        &graph,
        args.quantum,
        request,
        args.round_trip,
        connections,
        beat_len,
    );
    control.set_default_budget(Some(report.monitor_budget));

    let stdout = io::stdout();
    let mut out = stdout.lock();
    writeln!(out, "{}", format_startup(&report)).map_err(|e| e.to_string())?;
    out.flush().map_err(|e| e.to_string())?;
    let served = serve(&mut control, io::stdin().lock(), out);
    // the callback stops before the control side closes the take
    let _ = active.deactivate();
    drop(control);
    served.map_err(|e| format!("the control channel failed: {e}"))
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "-h" || a == "--help") {
        println!("{USAGE}");
        return;
    }
    let args = match parse_args(args) {
        Ok(args) => args,
        Err(e) => {
            eprintln!("redline-rec: {e}\n\n{USAGE}");
            exit(UNAVAILABLE);
        }
    };
    if let Err(e) = run(args) {
        eprintln!("redline-rec: {e}");
        exit(UNAVAILABLE);
    }
}
