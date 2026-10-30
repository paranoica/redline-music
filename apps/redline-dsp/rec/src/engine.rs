//! The audio side ([`Engine`]), the take writer thread and the control side ([`Control`]).
//!
//! Frames are counted by the backend: one counter for input and output, so beat frame 0 is
//! the first output frame of the cycle a take starts in, and the mic frame on the same count
//! is the one captured then. The take drops the first `offset` frames, the round trip, so
//! what the singer sang to beat frame `b` lands on take frame `b`.

use crate::budget::fit_latency;
use crate::protocol::{format, parse, Cause, Command, Dropout, Meters, Reply, TakeReport};
use crate::ring::{ring, Consumer, Producer};
use crate::wav::WavWriter;
use redline_dsp_core::effect::Rack;
use redline_dsp_core::filters::flush_tiny;
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{mpsc, Arc};
use std::thread::JoinHandle;
use std::time::Duration;

/// How long a new monitor rack takes to fade in over the old one.
pub const FADE_SECONDS: f32 = 0.005;

/// Frames the rack is run on at a time; a longer period is split.
const CHUNK: usize = 512;

/// Racks the control side may have handed over and not had back, the playing one included.
const MAX_RACKS: usize = 4;

const START: u8 = 1;
const END: u8 = 2;
const XRUN: u8 = 4;
const COUNTER_GAP: u8 = 8;
const OVERFLOW: u8 = 16;

/// Mic frames `rel..rel + len` counted from the take's start follow in the sample ring; the
/// flags say why frames before them are missing.
#[derive(Clone, Copy)]
struct Block {
    rel: u64,
    len: u32,
    flags: u8,
}

enum ToAudio {
    Start,
    Stop,
    Rack(Box<Rack>),
}

pub struct Config {
    pub rate: u32,
    /// Played from the first frame of each take, at `rate`.
    pub beat: Vec<[f32; 2]>,
    /// Mic frames the writer may fall behind by before a block is lost.
    pub ring_frames: usize,
}

impl Config {
    /// Ten seconds of ring.
    pub fn new(rate: u32, beat: Vec<[f32; 2]>) -> Config {
        Config {
            rate,
            beat,
            ring_frames: rate as usize * 10,
        }
    }
}

#[derive(Default)]
struct Shared {
    mic_peak: AtomicU32,
    out_peak: AtomicU32,
    xruns: AtomicU64,
    overflows: AtomicU64,
    take_frames: AtomicU64,
    /// The period the backend last reported; 0 until it does.
    quantum: AtomicU32,
    quit: AtomicBool,
}

/// What the backend's notification callbacks hold; they may run on any thread.
#[derive(Clone)]
pub struct XrunReporter(Arc<Shared>);

impl XrunReporter {
    pub fn report(&self) {
        self.0.xruns.fetch_add(1, Ordering::Relaxed);
    }

    /// The period the graph runs at now, for the meters.
    pub fn set_quantum(&self, frames: u32) {
        self.0.quantum.store(frames, Ordering::Relaxed);
    }
}

/// The audio side: [`Engine::cycle`] once a period. Nothing in it allocates, frees, locks or
/// waits.
pub struct Engine {
    fade_len: usize,
    beat: Vec<[f32; 2]>,
    rack: Box<Rack>,
    fading: Option<Box<Rack>>,
    fade_pos: usize,
    queued: Option<Box<Rack>>,
    commands: Consumer<ToAudio>,
    returns: Producer<Box<Rack>>,
    samples: Producer<f32>,
    blocks: Producer<Block>,
    shared: Arc<Shared>,
    wet: Vec<[f32; 2]>,
    old: Vec<[f32; 2]>,
    next_frame: Option<u64>,
    xruns_seen: u64,
    origin: Option<u64>,
    end: Option<u64>,
    pending: u8,
}

fn give_back(returns: &mut Producer<Box<Rack>>, rack: Box<Rack>) {
    // the control side never has more racks out than the ring holds; were it full anyway,
    // leaking beats freeing here
    if let Err(rack) = returns.push(rack) {
        std::mem::forget(rack);
    }
}

fn peak(meter: &AtomicU32, block: &[f32]) {
    let p = block
        .iter()
        .fold(0.0f32, |m, v| if v.abs() > m { v.abs() } else { m })
        // NaN never wins the fold; an infinite peak would go out as JSON null
        .min(f32::MAX);
    // a positive float's bits order as the float does
    meter.fetch_max(p.to_bits(), Ordering::Relaxed);
}

impl Engine {
    pub fn xrun_reporter(&self) -> XrunReporter {
        XrunReporter(self.shared.clone())
    }

    /// One period: `mic` through the monitor rack, plus the beat while a take runs, into
    /// the outputs; the dry mic to the take. `frame` is the counter of the period's first
    /// frame, shared by input and output; a jump in it is a gap in the take.
    pub fn cycle(&mut self, mic: &[f32], out_l: &mut [f32], out_r: &mut [f32], frame: u64) {
        let _ftz = flush_tiny();
        let n = mic.len().min(out_l.len()).min(out_r.len());
        out_l[n..].fill(0.0);
        out_r[n..].fill(0.0);
        let mic = &mic[..n];

        let xruns = self.shared.xruns.load(Ordering::Relaxed);
        if xruns != self.xruns_seen {
            self.xruns_seen = xruns;
            self.pending |= XRUN;
        }
        if self.next_frame.is_some_and(|next| frame > next) {
            self.pending |= COUNTER_GAP;
        }
        self.next_frame = Some(frame + n as u64);

        self.take_commands(frame);
        if let Some(end) = self.end {
            let block = Block {
                rel: end,
                len: 0,
                flags: END | self.pending,
            };
            if self.blocks.push(block).is_ok() {
                self.end = None;
                self.pending = 0;
            }
        }

        self.monitor(mic, &mut out_l[..n], &mut out_r[..n], frame);

        if let Some(origin) = self.origin {
            let rel = frame.saturating_sub(origin);
            if self.blocks.free() > 0 && self.samples.free() >= n {
                self.samples.push_slice(mic);
                let block = Block {
                    rel,
                    len: n as u32,
                    flags: self.pending,
                };
                let _ = self.blocks.push(block);
                self.pending = 0;
            } else {
                self.pending |= OVERFLOW;
                self.shared.overflows.fetch_add(1, Ordering::Relaxed);
            }
            self.shared
                .take_frames
                .store(rel + n as u64, Ordering::Relaxed);
        }
        peak(&self.shared.mic_peak, mic);
        peak(&self.shared.out_peak, &out_l[..n]);
        peak(&self.shared.out_peak, &out_r[..n]);
    }

    fn take_commands(&mut self, frame: u64) {
        while let Some(command) = self.commands.pop() {
            match command {
                ToAudio::Start => {
                    if self.origin.is_none() && self.end.is_none() {
                        self.origin = Some(frame);
                        self.pending = START;
                    }
                }
                ToAudio::Stop => {
                    if let Some(origin) = self.origin.take() {
                        self.end = Some(frame.saturating_sub(origin));
                    }
                }
                ToAudio::Rack(rack) => {
                    // one fade at a time; of those waiting, only the newest is played
                    if let Some(skipped) = self.queued.replace(rack) {
                        give_back(&mut self.returns, skipped);
                    }
                }
            }
        }
        if self.fading.is_none() {
            if let Some(rack) = self.queued.take() {
                self.fading = Some(std::mem::replace(&mut self.rack, rack));
                self.fade_pos = 0;
            }
        }
    }

    fn monitor(&mut self, mic: &[f32], out_l: &mut [f32], out_r: &mut [f32], frame: u64) {
        let mut done = 0;
        while done < mic.len() {
            let m = (mic.len() - done).min(CHUNK);
            let part = &mic[done..done + m];
            let wet = &mut self.wet[..m];
            for (f, &x) in wet.iter_mut().zip(part) {
                *f = [x, x];
            }
            self.rack.process(wet);
            if let Some(old) = self.fading.as_mut() {
                let dry = &mut self.old[..m];
                for (f, &x) in dry.iter_mut().zip(part) {
                    *f = [x, x];
                }
                old.process(dry);
                for (i, (w, d)) in wet.iter_mut().zip(dry.iter()).enumerate() {
                    let g = ((self.fade_pos + i) as f32 / self.fade_len as f32).min(1.0);
                    for ch in 0..2 {
                        w[ch] = (1.0 - g) * d[ch] + g * w[ch];
                    }
                }
                self.fade_pos += m;
                if self.fade_pos >= self.fade_len {
                    if let Some(old) = self.fading.take() {
                        give_back(&mut self.returns, old);
                    }
                }
            }
            for (i, w) in wet.iter().enumerate() {
                let mut f = *w;
                if let Some(origin) = self.origin {
                    let b = (frame + (done + i) as u64).saturating_sub(origin);
                    if let Some(beat) = self.beat.get(b as usize) {
                        f[0] += beat[0];
                        f[1] += beat[1];
                    }
                }
                out_l[done + i] = f[0];
                out_r[done + i] = f[1];
            }
            done += m;
        }
    }
}

struct TakeOpen {
    wav: WavWriter,
    path: PathBuf,
    offset: u64,
}

/// The writer's side of a take: `next` is the first mic frame, counted from the start, not
/// yet accounted for.
struct Take {
    wav: Option<WavWriter>,
    path: PathBuf,
    offset: u64,
    next: u64,
    dropouts: Vec<Dropout>,
    error: Option<String>,
    rate: u32,
    unpatched: u64,
}

fn causes(flags: u8) -> Vec<Cause> {
    [
        (XRUN, Cause::Xrun),
        (COUNTER_GAP, Cause::CounterGap),
        (OVERFLOW, Cause::Overflow),
    ]
    .into_iter()
    .filter(|(bit, _)| flags & bit != 0)
    .map(|(_, cause)| cause)
    .collect()
}

impl Take {
    fn io(&mut self, f: impl FnOnce(&mut WavWriter) -> io::Result<()>) {
        if self.error.is_some() {
            return;
        }
        if let Some(wav) = self.wav.as_mut() {
            if let Err(e) = f(wav) {
                self.error = Some(format!("writing {}: {e}", self.path.display()));
            }
        }
    }

    /// Silence for what is missing before `rel`, logged with why.
    fn gap_to(&mut self, rel: u64, flags: u8) {
        if rel > self.next {
            let from = self.next.max(self.offset);
            if rel > from {
                let frames = rel - from;
                self.io(|w| w.write_silence(frames));
                self.dropouts.push(Dropout {
                    start: from - self.offset,
                    frames,
                    causes: causes(flags),
                });
            }
            self.next = rel;
        } else if flags & (XRUN | OVERFLOW) != 0 && rel >= self.offset {
            self.dropouts.push(Dropout {
                start: rel - self.offset,
                frames: 0,
                causes: causes(flags),
            });
        }
    }

    fn write(&mut self, rel: u64, data: &[f32]) {
        let end = rel + data.len() as u64;
        let from = self.next.max(self.offset).max(rel);
        if end > from {
            let part = &data[(from - rel) as usize..];
            self.io(|w| w.write(part));
            self.unpatched += part.len() as u64;
            if self.unpatched >= self.rate as u64 {
                self.unpatched = 0;
                self.io(|w| w.patch());
            }
        }
        self.next = self.next.max(end);
    }

    fn finish(mut self) -> Result<TakeReport, String> {
        let wav = self.wav.take();
        let frames = wav.as_ref().map_or(0, WavWriter::frames);
        if let (None, Some(wav)) = (&self.error, wav) {
            if let Err(e) = wav.finish() {
                self.error = Some(format!("writing {}: {e}", self.path.display()));
            }
        }
        match self.error {
            Some(e) => Err(e),
            None => Ok(TakeReport {
                path: self.path,
                rate: self.rate,
                frames,
                offset: self.offset,
                dropouts: self.dropouts,
            }),
        }
    }
}

struct Writer {
    blocks: Consumer<Block>,
    samples: Consumer<f32>,
    open: mpsc::Receiver<TakeOpen>,
    done: mpsc::Sender<Result<TakeReport, String>>,
    shared: Arc<Shared>,
    rate: u32,
}

impl Writer {
    fn run(mut self) {
        let mut buf = vec![0.0f32; 4096];
        let mut take: Option<Take> = None;
        loop {
            let Some(block) = self.blocks.pop() else {
                if self.shared.quit.load(Ordering::Acquire) && self.blocks.is_empty() {
                    if let Some(t) = take.take() {
                        let _ = self.done.send(t.finish());
                    }
                    return;
                }
                std::thread::sleep(Duration::from_millis(2));
                continue;
            };
            if block.flags & START != 0 {
                // the control side sends the file before it tells the audio side to start
                take = self.open.recv().ok().map(|o| Take {
                    wav: Some(o.wav),
                    path: o.path,
                    offset: o.offset,
                    next: 0,
                    dropouts: Vec::new(),
                    error: None,
                    rate: self.rate,
                    unpatched: 0,
                });
            }
            if let Some(t) = take.as_mut() {
                t.gap_to(block.rel, block.flags);
            }
            let mut rel = block.rel;
            let mut left = block.len as usize;
            while left > 0 {
                // pushed before their block, so they are all there
                let got = self.samples.pop_slice(&mut buf[..left.min(4096)]);
                if let Some(t) = take.as_mut() {
                    t.write(rel, &buf[..got]);
                }
                rel += got as u64;
                left -= got;
            }
            if block.flags & END != 0 {
                if let Some(t) = take.take() {
                    let _ = self.done.send(t.finish());
                }
            }
        }
    }
}

/// The control side: builds racks, opens takes, reads meters. Dropping it ends the writer,
/// closing a take still open.
pub struct Control {
    rate: u32,
    to_audio: Producer<ToAudio>,
    returns: Consumer<Box<Rack>>,
    open: mpsc::Sender<TakeOpen>,
    done: mpsc::Receiver<Result<TakeReport, String>>,
    shared: Arc<Shared>,
    writer: Option<JoinHandle<()>>,
    racks_out: usize,
    returned: usize,
    recording: bool,
    stopping: bool,
    rack_latency: usize,
    default_budget: Option<usize>,
}

/// The two sides of an engine at `config.rate`, monitoring the mic dry until a rack is set.
pub fn engine(config: Config) -> Result<(Engine, Control), String> {
    let mut rack = Rack::from_json(r#"{"effects": []}"#).map_err(|e| e.to_string())?;
    rack.reset(config.rate as f32).map_err(|e| e.to_string())?;
    let shared = Arc::new(Shared::default());
    let (samples_tx, samples_rx) = ring(config.ring_frames);
    let (blocks_tx, blocks_rx) = ring((config.ring_frames / 32).max(16));
    let (to_audio, commands) = ring(16);
    let (returns_tx, returns_rx) = ring(2 * MAX_RACKS);
    let (open_tx, open_rx) = mpsc::channel();
    let (done_tx, done_rx) = mpsc::channel();
    let writer = Writer {
        blocks: blocks_rx,
        samples: samples_rx,
        open: open_rx,
        done: done_tx,
        shared: shared.clone(),
        rate: config.rate,
    };
    let writer = std::thread::Builder::new()
        .name("redline-rec-writer".into())
        .spawn(move || writer.run())
        .map_err(|e| format!("the take writer did not start: {e}"))?;
    let engine = Engine {
        fade_len: ((FADE_SECONDS * config.rate as f32) as usize).max(1),
        beat: config.beat,
        rack: Box::new(rack),
        fading: None,
        fade_pos: 0,
        queued: None,
        commands,
        returns: returns_tx,
        samples: samples_tx,
        blocks: blocks_tx,
        shared: shared.clone(),
        wet: vec![[0.0; 2]; CHUNK],
        old: vec![[0.0; 2]; CHUNK],
        next_frame: None,
        xruns_seen: 0,
        origin: None,
        end: None,
        pending: 0,
    };
    let control = Control {
        rate: config.rate,
        to_audio,
        returns: returns_rx,
        open: open_tx,
        done: done_rx,
        shared,
        writer: Some(writer),
        racks_out: 1,
        returned: 0,
        recording: false,
        stopping: false,
        rack_latency: 0,
        default_budget: None,
    };
    Ok((engine, control))
}

const BUSY: &str = "busy: the engine has not taken the last commands yet; try again";

impl Control {
    fn drain(&mut self) {
        while let Some(rack) = self.returns.pop() {
            drop(rack);
            self.racks_out -= 1;
            self.returned += 1;
        }
    }

    /// Drops the racks the engine handed back; how many since the last call.
    pub fn collect(&mut self) -> usize {
        self.drain();
        std::mem::take(&mut self.returned)
    }

    /// Builds the rack, resets it at the engine's rate, switches effects off from its end
    /// until its latency fits `budget` frames, and hands it over; its latency and the
    /// effects switched off.
    pub fn set_rack_json(
        &mut self,
        json: &str,
        budget: Option<usize>,
    ) -> Result<(usize, Vec<&'static str>), String> {
        self.drain();
        if self.racks_out >= MAX_RACKS || self.to_audio.free() == 0 {
            return Err(format!(
                "{BUSY} (racks are swapped one fade, {FADE_SECONDS} s, at a time)"
            ));
        }
        let mut rack = Rack::from_json(json).map_err(|e| e.to_string())?;
        rack.reset(self.rate as f32).map_err(|e| e.to_string())?;
        let dropped = budget.map_or_else(Vec::new, |b| fit_latency(&mut rack, b));
        let latency = rack.latency();
        if self.to_audio.push(ToAudio::Rack(Box::new(rack))).is_err() {
            return Err(BUSY.into());
        }
        self.racks_out += 1;
        self.rack_latency = latency;
        Ok((latency, dropped))
    }

    /// The budget a `set_rack` command without one is held to.
    pub fn set_default_budget(&mut self, budget: Option<usize>) {
        self.default_budget = budget;
    }

    /// Records the mic into a new file at `path` from the next period on, beat frame 0 at
    /// take frame 0 once `offset`, the round trip in frames, is dropped.
    pub fn start(&mut self, path: &Path, offset: u64) -> Result<(), String> {
        if self.stopping {
            // a stop that timed out: the writer may have finished the take since
            match self.done.try_recv() {
                Ok(_) | Err(mpsc::TryRecvError::Disconnected) => {
                    self.recording = false;
                    self.stopping = false;
                }
                Err(mpsc::TryRecvError::Empty) => {
                    return Err(
                        "the last take is still being finished; stop again to wait for it".into(),
                    )
                }
            }
        }
        if self.recording {
            return Err("a take is already being recorded".into());
        }
        if path.exists() {
            return Err(format!("{} already exists", path.display()));
        }
        if self.to_audio.free() == 0 {
            return Err(BUSY.into());
        }
        let wav = WavWriter::create(path, self.rate, 1)
            .map_err(|e| format!("cannot write {}: {e}", path.display()))?;
        let open = TakeOpen {
            wav,
            path: path.to_path_buf(),
            offset,
        };
        if self.open.send(open).is_err() {
            return Err("the take writer has stopped".into());
        }
        // checked for room above, and only this side pushes
        let _ = self.to_audio.push(ToAudio::Start);
        self.shared.take_frames.store(0, Ordering::Relaxed);
        self.recording = true;
        Ok(())
    }

    /// Ends the take at the next period; [`Control::wait_take`] has its report.
    /// Once asked, asking again only goes back to waiting for the take.
    pub fn request_stop(&mut self) -> Result<(), String> {
        if self.stopping {
            return Ok(());
        }
        if !self.recording {
            return Err("no take is being recorded".into());
        }
        if self.to_audio.push(ToAudio::Stop).is_err() {
            return Err(BUSY.into());
        }
        self.stopping = true;
        Ok(())
    }

    pub fn wait_take(&mut self, timeout: Duration) -> Result<TakeReport, String> {
        if !self.stopping {
            return Err("no take is being stopped".into());
        }
        let report = match self.done.recv_timeout(timeout) {
            Ok(report) => report,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                return Err(format!(
                    "the take did not finish within {} s: is the audio running?",
                    timeout.as_secs_f32()
                ))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => Err("the take writer stopped".into()),
        };
        self.recording = false;
        self.stopping = false;
        report
    }

    /// Peaks since the last call.
    pub fn meters(&mut self) -> Meters {
        let s = &self.shared;
        Meters {
            mic_peak: f32::from_bits(s.mic_peak.swap(0, Ordering::Relaxed)),
            out_peak: f32::from_bits(s.out_peak.swap(0, Ordering::Relaxed)),
            xruns: s.xruns.load(Ordering::Relaxed),
            overflows: s.overflows.load(Ordering::Relaxed),
            recording: self.recording,
            take_frames: s.take_frames.load(Ordering::Relaxed),
            rack_latency: self.rack_latency,
            quantum: Some(s.quantum.load(Ordering::Relaxed)).filter(|&q| q > 0),
        }
    }

    fn stop(&mut self) -> Result<TakeReport, String> {
        self.request_stop()?;
        self.wait_take(Duration::from_secs(10))
    }

    pub fn handle(&mut self, command: Command) -> Reply {
        let reply = match command {
            Command::Start { path, offset } => {
                self.start(&path, offset).map(|()| Reply::Start { path })
            }
            Command::Stop => self.stop().map(|take| Reply::Stop { take }),
            Command::SetRack { rack, budget } => self
                .set_rack_json(&rack.to_string(), budget.or(self.default_budget))
                .map(|(latency, dropped)| Reply::SetRack {
                    latency,
                    dropped: dropped.into_iter().map(String::from).collect(),
                }),
            Command::Meters => Ok(Reply::Meters(self.meters())),
            Command::Quit => {
                if self.recording && !self.stopping {
                    let _ = self.stop();
                }
                Ok(Reply::Quit)
            }
        };
        reply.unwrap_or_else(|error| Reply::Error { error })
    }
}

impl Drop for Control {
    fn drop(&mut self) {
        self.shared.quit.store(true, Ordering::Release);
        if let Some(writer) = self.writer.take() {
            let _ = writer.join();
        }
    }
}

/// Answers each command line of `input` with a reply line on `output` until quit or the end
/// of the input.
pub fn serve(control: &mut Control, input: impl BufRead, mut output: impl Write) -> io::Result<()> {
    for line in input.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let (reply, quit) = match parse(&line) {
            Ok(command) => {
                let quit = command == Command::Quit;
                (control.handle(command), quit)
            }
            Err(error) => (Reply::Error { error }, false),
        };
        writeln!(output, "{}", format(&reply))?;
        output.flush()?;
        if quit {
            break;
        }
    }
    Ok(())
}
