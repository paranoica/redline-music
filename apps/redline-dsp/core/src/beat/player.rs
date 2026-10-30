//! Plays a score: voice pools per sound, a rack per sound and a master rack, with every
//! sound delayed so all of them come out as late as the slowest rack.
//!
//! Each sound's voices are summed in the order their events start, so a block size or a
//! seek cannot change the sum. A sound's compensation delay sits before its rack: the dry
//! signal goes through a ring, read `latency() - own` frames back, which is the same as
//! delaying the rack's output and lets a swapped rack and the one fading out read the same
//! ring at their own delays. A sidechain's side is read from its sound's ring at the
//! listening sound's delay, so it stays in step with the sound it ducks.

use super::events::EventTable;
use super::instruments::{Instrument, Kind};
use super::score::Score;
use super::voices::{self, sampler_for, Ctx, SampleBank, Voice};
use crate::effect::{Rack, MAX_BLOCK};
use crate::filters::flush_tiny;

/// The most frames the racks may delay the mix by, sound racks and master together.
pub const MAX_LATENCY: usize = 1 << 17;

/// The fade a voice takes when its event is cut.
const CUT_S: f64 = 0.005;
/// A swapped rack fades in over this, the old one out.
const CROSSFADE_S: f64 = 0.010;
/// A gain or pan change from a new score glides over this.
const GAIN_RAMP_S: f64 = 0.010;
const RENDER_BLOCK: usize = 512;
/// The most a render runs past the last voice's end for the racks' tails.
const TAIL_S: f64 = 4.0;
/// A render ends once the mix has stayed under [`QUIET`] this long past the song.
const QUIET_S: f64 = 0.5;
const QUIET: f32 = 1e-6;
/// A library index no bank holds: the sampler plays silence.
const NO_LIBRARY: usize = usize::MAX;

struct Pool {
    voices: Vec<Box<dyn Voice>>,
    busy: Vec<bool>,
    /// The busy slots, oldest event first.
    order: Vec<usize>,
}

impl Pool {
    fn new(voices: Vec<Box<dyn Voice>>) -> Pool {
        let n = voices.len();
        Pool {
            voices,
            busy: vec![false; n],
            order: Vec::with_capacity(n),
        }
    }

    /// A free slot, or the one with the oldest event if none is free.
    fn take(&mut self) -> usize {
        match self.busy.iter().position(|b| !b) {
            Some(s) => s,
            None => self.order.remove(0),
        }
    }

    fn keep(&mut self, slot: usize, alive: bool) {
        self.busy[slot] = alive;
        if alive {
            self.order.push(slot);
        }
    }

    fn release_all(&mut self) {
        self.busy.fill(false);
        self.order.clear();
    }

    fn idle(&self) -> bool {
        self.order.is_empty()
    }
}

/// Left and right gain from the dB and pan, gliding to a new target linearly.
#[derive(Clone, Copy)]
struct Gain {
    now: [f32; 2],
    target: [f32; 2],
    step: [f32; 2],
    left: u32,
}

/// Equal power, with the centre at unity: the gain of an unpanned sound is its gain_db.
fn pan_gains(gain_db: f32, pan: f32) -> [f32; 2] {
    let g = 10f64.powf(gain_db as f64 / 20.0);
    let theta = (pan.clamp(-1.0, 1.0) as f64 + 1.0) * std::f64::consts::FRAC_PI_4;
    let s = g * std::f64::consts::SQRT_2;
    [(s * theta.cos()) as f32, (s * theta.sin()) as f32]
}

impl Gain {
    fn new(target: [f32; 2]) -> Gain {
        Gain {
            now: target,
            target,
            step: [0.0; 2],
            left: 0,
        }
    }

    fn glide(&mut self, target: [f32; 2], frames: u32) {
        if target == self.target {
            return;
        }
        self.target = target;
        self.left = frames.max(1);
        let n = self.left as f32;
        self.step = [(target[0] - self.now[0]) / n, (target[1] - self.now[1]) / n];
    }

    fn snap(&mut self) {
        self.now = self.target;
        self.left = 0;
    }

    fn apply(&mut self, block: &mut [[f32; 2]]) {
        for f in block {
            if self.left > 0 {
                self.left -= 1;
                if self.left == 0 {
                    self.now = self.target;
                } else {
                    self.now[0] += self.step[0];
                    self.now[1] += self.step[1];
                }
            }
            f[0] *= self.now[0];
            f[1] *= self.now[1];
        }
    }
}

struct Channel {
    name: String,
    instrument: &'static Instrument,
    library: Option<String>,
    params: Vec<f32>,
    pool: Pool,
    /// This block's voices, before gain.
    dry: Vec<[f32; 2]>,
    /// The recent dry blocks, when any sound is delayed.
    ring: Vec<[f32; 2]>,
    delay: usize,
    rack: Rack,
    /// The rack a swap fades out, kept after the fade until the next swap hands it back.
    outgoing: Option<Rack>,
    outgoing_delay: usize,
    fade_left: u32,
    gain: Gain,
    sidechain: Option<usize>,
}

/// Plays a score block by block. `process` and `seek` allocate nothing; `new`,
/// `set_score`, `rebuild_samplers` and loading the bank do.
pub struct Player {
    rate: f32,
    table: EventTable,
    /// Per event, a frame by which its voice is surely silent.
    last: Vec<u64>,
    cursor: usize,
    position: u64,
    channels: Vec<Channel>,
    master: Rack,
    bank: SampleBank,
    /// The delay every sound comes out with: the slowest sound rack's latency.
    comp: usize,
    mask: usize,
    write: usize,
    crossfade: u32,
    gain_ramp: u32,
    wet: Vec<[f32; 2]>,
    wet_old: Vec<[f32; 2]>,
    side: Vec<[f32; 2]>,
    side_old: Vec<[f32; 2]>,
}

/// A whole song rendered with its tail.
pub struct Render {
    pub mix: Vec<[f32; 2]>,
    /// One a sound, after its gain, pan, rack and compensation, before the master rack.
    pub stems: Vec<Vec<[f32; 2]>>,
    /// Frames of the song itself; the mix and stems run on past it by the tail.
    pub length: u64,
}

fn rack_at(json: &str, rate: f32, what: &str) -> Result<Rack, String> {
    let mut rack = Rack::from_json(json).map_err(|e| format!("{what}: {e}"))?;
    rack.reset(rate).map_err(|e| format!("{what}: {e}"))?;
    Ok(rack)
}

fn pool_size(inst: &Instrument) -> usize {
    // one more than can ring, so a cut voice can finish its fade
    if inst.mono {
        2
    } else {
        inst.max_voices + 1
    }
}

fn make_voices(
    inst: &Instrument,
    library: Option<&str>,
    bank: &SampleBank,
    rate: f32,
) -> Result<Vec<Box<dyn Voice>>, String> {
    (0..pool_size(inst))
        .map(|_| {
            if inst.name == "sampler" {
                let index = library.and_then(|l| bank.library(l)).unwrap_or(NO_LIBRARY);
                Ok(sampler_for(rate, index))
            } else {
                voices::make(inst.name, rate).ok_or_else(|| format!("no voice for {}", inst.name))
            }
        })
        .collect()
}

fn last_frames(table: &EventTable, score: &Score, rate: f32) -> Vec<u64> {
    let rate = rate as f64;
    let cut = (CUT_S * rate).ceil() as u64 + 1;
    table
        .events
        .iter()
        .map(|e| {
            let inst = score.sounds[e.sound as usize].instrument;
            let life = inst.max_life_s as f64 * rate;
            let natural = match inst.kind {
                Kind::Note => e.off.unwrap_or(e.start) + life.round() as u64,
                Kind::Drum => e.start + (life * (e.decay as f64).max(0.0)).round() as u64,
            };
            let cut_off = e.end.map_or(u64::MAX, |end| end + cut);
            natural.min(cut_off) + 2
        })
        .collect()
}

/// Frames of `dry` as they were `delay` frames ago, from the ring written up to `at + n`.
fn tap(dry: &[[f32; 2]], ring: &[[f32; 2]], at: usize, delay: usize, out: &mut [[f32; 2]]) {
    if delay == 0 {
        out.copy_from_slice(&dry[..out.len()]);
        return;
    }
    let mask = ring.len() - 1;
    let from = at.wrapping_sub(delay);
    for (i, f) in out.iter_mut().enumerate() {
        *f = ring[from.wrapping_add(i) & mask];
    }
}

impl Player {
    pub fn new(score: &Score, rate: f32, bank: SampleBank) -> Result<Player, String> {
        if !(rate.is_finite() && rate > 0.0) {
            return Err(format!("the rate {rate} is not a positive number"));
        }
        let table = EventTable::build(score, rate);
        let last = last_frames(&table, score, rate);
        let master = rack_at(&score.mix.master, rate, "mix.master")?;
        let mut racks = Vec::with_capacity(score.sounds.len());
        for (sound, mix) in score.sounds.iter().zip(&score.mix.sounds) {
            racks.push(rack_at(
                &mix.rack,
                rate,
                &format!("mix.sounds.{}.rack", sound.name),
            )?);
        }
        let comp = racks.iter().map(Rack::latency).max().unwrap_or(0);
        let total = comp + master.latency();
        if total > MAX_LATENCY {
            return Err(format!(
                "the racks delay the mix by {total} frames, more than the {MAX_LATENCY} a beat \
                 takes"
            ));
        }
        let ring_len = if comp > 0 {
            (comp + MAX_BLOCK).next_power_of_two()
        } else {
            0
        };
        let mut channels = Vec::with_capacity(score.sounds.len());
        for ((sound, mix), rack) in score.sounds.iter().zip(&score.mix.sounds).zip(racks) {
            let voices = make_voices(sound.instrument, sound.library.as_deref(), &bank, rate)?;
            channels.push(Channel {
                name: sound.name.clone(),
                instrument: sound.instrument,
                library: sound.library.clone(),
                params: sound.params.clone(),
                pool: Pool::new(voices),
                dry: vec![[0.0; 2]; MAX_BLOCK],
                ring: vec![[0.0; 2]; ring_len],
                delay: comp - rack.latency(),
                rack,
                outgoing: None,
                outgoing_delay: 0,
                fade_left: 0,
                gain: Gain::new(pan_gains(mix.gain_db, mix.pan)),
                sidechain: mix.sidechain,
            });
        }
        Ok(Player {
            rate,
            table,
            last,
            cursor: 0,
            position: 0,
            channels,
            master,
            bank,
            comp,
            mask: ring_len.wrapping_sub(1),
            write: 0,
            crossfade: ((CROSSFADE_S * rate as f64).round() as u32).max(1),
            gain_ramp: ((GAIN_RAMP_S * rate as f64).round() as u32).max(1),
            wet: vec![[0.0; 2]; MAX_BLOCK],
            wet_old: vec![[0.0; 2]; MAX_BLOCK],
            side: vec![[0.0; 2]; MAX_BLOCK],
            side_old: vec![[0.0; 2]; MAX_BLOCK],
        })
    }

    pub fn rate(&self) -> f32 {
        self.rate
    }

    /// The next frame `process` plays.
    pub fn position(&self) -> u64 {
        self.position
    }

    pub fn table(&self) -> &EventTable {
        &self.table
    }

    /// Frames from a frame of the song to the mix's frame that carries it: the slowest
    /// sound rack plus the master rack. An offline render drops this many from the front.
    pub fn latency(&self) -> usize {
        self.comp + self.master.latency()
    }

    /// The delay every stem comes out with, the slowest sound rack's latency.
    pub fn stem_latency(&self) -> usize {
        self.comp
    }

    /// Voices playing now, every sound together.
    pub fn voices(&self) -> usize {
        self.channels.iter().map(|c| c.pool.order.len()).sum()
    }

    pub fn bank(&self) -> &SampleBank {
        &self.bank
    }

    /// The bank, to load zones into between blocks; then [`Player::rebuild_samplers`] so the
    /// samplers find their libraries.
    pub fn bank_mut(&mut self) -> &mut SampleBank {
        &mut self.bank
    }

    /// Makes the sampler voices anew for the bank's libraries. Allocates, and silences the
    /// samplers' ringing voices: between blocks only.
    pub fn rebuild_samplers(&mut self) -> Result<(), String> {
        for ch in &mut self.channels {
            if ch.instrument.name == "sampler" {
                let voices =
                    make_voices(ch.instrument, ch.library.as_deref(), &self.bank, self.rate)?;
                ch.pool = Pool::new(voices);
            }
        }
        Ok(())
    }

    /// Overwrites `out` with the next frames of the mix. Past the song's end the tails ring
    /// out, then silence.
    pub fn process(&mut self, out: &mut [[f32; 2]]) {
        let _ftz = flush_tiny();
        for chunk in out.chunks_mut(MAX_BLOCK) {
            self.block(chunk, None, 0);
        }
    }

    /// As `process`, also writing each sound's part of the mix (before the master rack) into
    /// the front of `stems[sound]`, each of which must be at least as long as `out`.
    pub fn process_stems(&mut self, out: &mut [[f32; 2]], stems: &mut [Vec<[f32; 2]>]) {
        assert!(
            stems.len() == self.channels.len() && stems.iter().all(|s| s.len() >= out.len()),
            "a stem a sound, each as long as the block"
        );
        let _ftz = flush_tiny();
        for (k, chunk) in out.chunks_mut(MAX_BLOCK).enumerate() {
            self.block(chunk, Some(stems), k * MAX_BLOCK);
        }
    }

    fn block(&mut self, out: &mut [[f32; 2]], mut stems: Option<&mut [Vec<[f32; 2]>]>, at: usize) {
        let n = out.len();
        let ctx = Ctx {
            rate: self.rate,
            bank: &self.bank,
        };
        for ch in &mut self.channels {
            let dry = &mut ch.dry[..n];
            dry.fill([0.0; 2]);
            let pool = &mut ch.pool;
            let mut i = 0;
            while i < pool.order.len() {
                let s = pool.order[i];
                if pool.voices[s].render(dry, &ctx) {
                    i += 1;
                } else {
                    pool.busy[s] = false;
                    pool.order.remove(i);
                }
            }
        }
        let end = self.position + n as u64;
        while let Some(e) = self.table.events.get(self.cursor) {
            if e.start >= end {
                break;
            }
            let ch = &mut self.channels[e.sound as usize];
            let from = e.start.saturating_sub(self.position) as usize;
            let s = ch.pool.take();
            let voice = &mut ch.pool.voices[s];
            voice.start(e, &ch.params, &ctx);
            let alive = voice.render(&mut ch.dry[from..n], &ctx);
            ch.pool.keep(s, alive);
            self.cursor += 1;
        }

        if self.comp > 0 {
            for ch in &mut self.channels {
                for (i, f) in ch.dry[..n].iter().enumerate() {
                    ch.ring[self.write.wrapping_add(i) & self.mask] = *f;
                }
            }
        }

        out.fill([0.0; 2]);
        for k in 0..self.channels.len() {
            let (delay, old_delay, fading) = {
                let ch = &self.channels[k];
                (ch.delay, ch.outgoing_delay, ch.fade_left > 0)
            };
            let side_len = if let Some(j) = self.channels[k].sidechain {
                let src = &self.channels[j];
                tap(&src.dry, &src.ring, self.write, delay, &mut self.side[..n]);
                if fading {
                    tap(
                        &src.dry,
                        &src.ring,
                        self.write,
                        old_delay,
                        &mut self.side_old[..n],
                    );
                }
                n
            } else {
                0
            };
            let ch = &mut self.channels[k];
            let wet = &mut self.wet[..n];
            tap(&ch.dry, &ch.ring, self.write, delay, wet);
            let before = ch.gain;
            ch.gain.apply(wet);
            ch.rack.process_with(wet, &self.side[..side_len]);
            if fading {
                if let Some(old) = ch.outgoing.as_mut() {
                    let wet_old = &mut self.wet_old[..n];
                    tap(&ch.dry, &ch.ring, self.write, old_delay, wet_old);
                    let mut gain = before;
                    gain.apply(wet_old);
                    old.process_with(wet_old, &self.side_old[..side_len]);
                    let total = self.crossfade as f32;
                    for (f, o) in wet.iter_mut().zip(wet_old.iter()) {
                        if ch.fade_left == 0 {
                            break;
                        }
                        let a = ch.fade_left as f32 / total;
                        f[0] = f[0] * (1.0 - a) + o[0] * a;
                        f[1] = f[1] * (1.0 - a) + o[1] * a;
                        ch.fade_left -= 1;
                    }
                } else {
                    ch.fade_left = 0;
                }
            }
            for (o, w) in out.iter_mut().zip(wet.iter()) {
                o[0] += w[0];
                o[1] += w[1];
            }
            if let Some(stems) = stems.as_deref_mut() {
                stems[k][at..at + n].copy_from_slice(wet);
            }
        }
        self.master.process(out);
        self.write = self.write.wrapping_add(n);
        self.position = end;
    }

    /// Plays on from `frame`: every voice whose event covers it is started and run forward
    /// to it, so the voices sound as they would have there. The racks and delays are not
    /// cleared: what they hold from before rings out.
    pub fn seek(&mut self, frame: u64) {
        let _ftz = flush_tiny();
        for ch in &mut self.channels {
            ch.pool.release_all();
            ch.gain.snap();
        }
        self.position = frame;
        self.cursor = self.table.events.partition_point(|e| e.start < frame);
        let ctx = Ctx {
            rate: self.rate,
            bank: &self.bank,
        };
        for (e, &last) in self.table.events[..self.cursor].iter().zip(&self.last) {
            if last <= frame {
                continue;
            }
            let ch = &mut self.channels[e.sound as usize];
            let s = ch.pool.take();
            let voice = &mut ch.pool.voices[s];
            voice.start(e, &ch.params, &ctx);
            let mut left = frame - e.start;
            let mut alive = true;
            while left > 0 && alive {
                let m = left.min(MAX_BLOCK as u64) as usize;
                let scratch = &mut ch.dry[..m];
                scratch.fill([0.0; 2]);
                alive = voice.render(scratch, &ctx);
                left -= m as u64;
            }
            ch.pool.keep(s, alive);
        }
    }

    /// Takes a new score with the same sounds (names, instruments and libraries): its
    /// params, gains, pans, sidechains and events, from the current position on. Ringing
    /// voices play on as they began. Racks are not read from it; see [`Player::set_rack`].
    /// Any other change is refused with "rebuild": the host makes a new player.
    pub fn set_score(&mut self, score: &Score) -> Result<(), String> {
        let same = score.sounds.len() == self.channels.len()
            && score.sounds.iter().zip(&self.channels).all(|(s, ch)| {
                s.name == ch.name
                    && std::ptr::eq(s.instrument, ch.instrument)
                    && s.library == ch.library
            });
        if !same {
            return Err("rebuild: the sounds' names, instruments or libraries changed".to_string());
        }
        self.table = EventTable::build(score, self.rate);
        self.last = last_frames(&self.table, score, self.rate);
        let position = self.position;
        self.cursor = self.table.events.partition_point(|e| e.start < position);
        for ((ch, sound), mix) in self
            .channels
            .iter_mut()
            .zip(&score.sounds)
            .zip(&score.mix.sounds)
        {
            ch.params.copy_from_slice(&sound.params);
            ch.gain
                .glide(pan_gains(mix.gain_db, mix.pan), self.gain_ramp);
            ch.sidechain = mix.sidechain;
        }
        Ok(())
    }

    /// Swaps a sound's rack, already reset at the player's rate, fading the old one out over
    /// 10 ms inside `process`. A rack is refused if it is later than the player compensates
    /// for ([`Player::stem_latency`]); a longer one needs a new player.
    ///
    /// The rack being faded out keeps running, so what comes back is the rack this swap
    /// retires: the one a previous swap faded out, None on a sound's first swap. A swap
    /// during a fade keeps whichever of the two racks is heard more and retires the other.
    pub fn set_rack(&mut self, sound: usize, rack: Rack) -> Result<Option<Rack>, String> {
        let comp = self.comp;
        let crossfade = self.crossfade;
        let ch = self
            .channels
            .get_mut(sound)
            .ok_or_else(|| format!("no sound {sound}"))?;
        if rack.latency() > comp {
            return Err(format!(
                "rebuild: the rack's latency of {} frames is over the {comp} the player \
                 compensates",
                rack.latency()
            ));
        }
        let delay = comp - rack.latency();
        let new_heard_less = ch.fade_left * 2 > crossfade;
        let retired = if ch.fade_left > 0 && new_heard_less {
            // the old rack stays the one fading out
            Some(std::mem::replace(&mut ch.rack, rack))
        } else {
            let current = std::mem::replace(&mut ch.rack, rack);
            ch.outgoing_delay = ch.delay;
            ch.outgoing.replace(current)
        };
        ch.delay = delay;
        ch.fade_left = crossfade;
        Ok(retired)
    }
}

/// The whole song and its tail, from frame 0 of the song: the racks' latency is dropped
/// from the front. Runs until every voice is done and the mix has stayed under -120 dB for
/// half a second past the song (at most 4 s past the last voice), then drops that quiet end.
pub fn render(score: &Score, rate: f32, bank: SampleBank) -> Result<Render, String> {
    let mut player = Player::new(score, rate, bank)?;
    let latency = player.latency() as u64;
    let stem_latency = player.stem_latency();
    let song = player.table.length;
    let voices_end = player.last.iter().copied().max().unwrap_or(0).max(song);
    let cap = voices_end + (TAIL_S * rate as f64).round() as u64;
    let quiet_for = (QUIET_S * rate as f64).round() as u64;
    let sounds = player.channels.len();

    let mut mix: Vec<[f32; 2]> = Vec::new();
    let mut stems: Vec<Vec<[f32; 2]>> = vec![Vec::new(); sounds];
    let mut out = vec![[0.0f32; 2]; RENDER_BLOCK];
    let mut parts = vec![vec![[0.0f32; 2]; RENDER_BLOCK]; sounds];
    // in song frames: one past the last loud frame, and the quiet run since
    let mut loud_end: u64 = 0;
    let mut quiet: u64 = 0;
    loop {
        let done = (mix.len() as u64).saturating_sub(latency);
        let raw_past = mix.len() as u64 >= latency;
        if raw_past && done >= song {
            let settled = player.cursor == player.table.events.len()
                && player.channels.iter().all(|c| c.pool.idle())
                && quiet >= quiet_for;
            if settled || done >= cap {
                break;
            }
        }
        player.process_stems(&mut out, &mut parts);
        for (i, f) in out.iter().enumerate() {
            let raw = mix.len() as u64 + i as u64;
            if raw < latency {
                continue;
            }
            let t = raw - latency;
            if f[0].abs() >= QUIET || f[1].abs() >= QUIET {
                loud_end = t + 1;
                quiet = 0;
            } else if t >= song {
                quiet += 1;
            }
        }
        mix.extend_from_slice(&out);
        for (stem, part) in stems.iter_mut().zip(&parts) {
            stem.extend_from_slice(part);
        }
    }
    let length = song.max(loud_end) as usize;
    let latency = latency as usize;
    mix.truncate(latency + length);
    mix.drain(..latency);
    for stem in &mut stems {
        stem.truncate(stem_latency + length);
        stem.drain(..stem_latency);
    }
    Ok(Render {
        mix,
        stems,
        length: song,
    })
}
