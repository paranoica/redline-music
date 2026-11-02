//! Recorded zones and the voice that plays them.

use super::drums::{Fall, Gate as HitGate};
use super::fm::{param, Gate};
use super::{Ctx, Voice};
use crate::beat::events::Event;
use crate::beat::instruments::SAMPLED;
use std::ops::RangeInclusive;

const TUNE: usize = 0;
const ATTACK: usize = 1;
const RELEASE: usize = 2;

/// The most i16 values a bank holds, all zones together: 96 MiB, about eight minutes of
/// stereo at 48 kHz. A stereo frame counts twice.
pub const SAMPLE_BUDGET: usize = 48 * 1024 * 1024;

/// One recording, played for the notes and velocities nearest to it.
struct Zone {
    root: f64,
    /// MIDI velocities, 0..=127, both ends included.
    vel_lo: u8,
    vel_hi: u8,
    rate: f64,
    channels: usize,
    /// Interleaved when stereo.
    samples: Vec<i16>,
}

impl Zone {
    fn frames(&self) -> usize {
        self.samples.len() / self.channels
    }

    fn at(&self, frame: usize, channel: usize) -> f64 {
        let frame = frame.min(self.frames() - 1);
        let channel = channel.min(self.channels - 1);
        self.samples[frame * self.channels + channel] as f64 / 32768.0
    }
}

/// The recorded zones a sampler plays, loaded by the host before playing (loading
/// allocates). A library is found by name; voices refer to it by its index, which stays
/// put until [`SampleBank::clear`].
pub struct SampleBank {
    names: Vec<String>,
    libraries: Vec<Vec<Zone>>,
    used: usize,
    budget: usize,
}

impl Default for SampleBank {
    fn default() -> Self {
        SampleBank::with_budget(SAMPLE_BUDGET)
    }
}

impl SampleBank {
    /// An empty bank that holds at most `budget` i16 values.
    pub fn with_budget(budget: usize) -> SampleBank {
        SampleBank {
            names: Vec::new(),
            libraries: Vec::new(),
            used: 0,
            budget,
        }
    }

    /// Add a zone to the library, which is made if it is new. `vel` is in MIDI velocities;
    /// `samples` are interleaved when `channels` is 2; `rate` is theirs, the voice corrects
    /// for the render's.
    pub fn load(
        &mut self,
        library: &str,
        root: u8,
        vel: RangeInclusive<u8>,
        rate: f32,
        samples: &[i16],
        channels: usize,
    ) -> Result<(), String> {
        let (vel_lo, vel_hi) = vel.into_inner();
        if library.is_empty() {
            return Err("a zone needs a library name".to_string());
        }
        if root > 127 {
            return Err(format!("root note {root} is past 127"));
        }
        if vel_lo > vel_hi || vel_hi > 127 {
            return Err(format!(
                "velocity range {vel_lo}..{vel_hi} is not within 0..127"
            ));
        }
        if !(rate.is_finite() && rate > 0.0) {
            return Err(format!("sample rate {rate} is not a positive number"));
        }
        if !(1..=2).contains(&channels) {
            return Err(format!("{channels} channels; a zone is mono or stereo"));
        }
        if samples.is_empty() || !samples.len().is_multiple_of(channels) {
            return Err(format!(
                "{} samples do not make whole {channels}-channel frames",
                samples.len()
            ));
        }
        if self.used + samples.len() > self.budget {
            return Err(format!(
                "the zone needs {} samples, but {} of the {} sample budget are used",
                samples.len(),
                self.used,
                self.budget
            ));
        }
        let index = self.library(library).unwrap_or_else(|| {
            self.names.push(library.to_string());
            self.libraries.push(Vec::new());
            self.names.len() - 1
        });
        self.libraries[index].push(Zone {
            root: root as f64,
            vel_lo,
            vel_hi,
            rate: rate as f64,
            channels,
            samples: samples.to_vec(),
        });
        self.used += samples.len();
        Ok(())
    }

    /// The index of the named library, for [`SampleBank::voice`].
    pub fn library(&self, name: &str) -> Option<usize> {
        self.names.iter().position(|n| n == name)
    }

    /// Drop every zone and library name.
    pub fn clear(&mut self) {
        self.names.clear();
        self.libraries.clear();
        self.used = 0;
    }

    /// The i16 values held.
    pub fn used(&self) -> usize {
        self.used
    }

    pub fn budget(&self) -> usize {
        self.budget
    }

    /// A sampler voice that plays the library of that index.
    pub fn voice(rate: f32, library: usize) -> Box<dyn Voice> {
        sampler_for(rate, library)
    }

    fn zone(&self, library: usize, zone: usize) -> Option<&Zone> {
        self.libraries.get(library)?.get(zone)
    }

    /// The zone for the note and velocity: inside the velocity layer if one holds it (else
    /// the nearest layer), then the nearest root; the first loaded on a tie.
    fn pick(&self, library: usize, note: f64, vel: u8) -> Option<usize> {
        let zones = self.libraries.get(library)?;
        let key = |z: &Zone| {
            let off = z
                .vel_lo
                .saturating_sub(vel)
                .max(vel.saturating_sub(z.vel_hi));
            (off, (z.root - note).abs())
        };
        zones
            .iter()
            .enumerate()
            .min_by(|a, b| {
                let (ka, kb) = (key(a.1), key(b.1));
                ka.0.cmp(&kb.0).then(ka.1.total_cmp(&kb.1))
            })
            .map(|(i, _)| i)
    }
}

#[derive(Default)]
struct Sampler {
    library: usize,
    zone: Option<usize>,
    active: bool,
    n: u64,
    gate: Gate,
    vel: f64,
    /// Zone frames a render frame.
    step: f64,
}

impl Sampler {
    /// Cubic Hermite between the four frames around `pos`.
    fn read(zone: &Zone, pos: f64, channel: usize) -> f64 {
        let i = pos.floor() as usize;
        let t = pos - i as f64;
        let y0 = zone.at(i.saturating_sub(1), channel);
        let y1 = zone.at(i, channel);
        let y2 = zone.at(i + 1, channel);
        let y3 = zone.at(i + 2, channel);
        let c1 = 0.5 * (y2 - y0);
        let c2 = y0 - 2.5 * y1 + 2.0 * y2 - 0.5 * y3;
        let c3 = 0.5 * (y3 - y0) + 1.5 * (y1 - y2);
        ((c3 * t + c2) * t + c1) * t + y1
    }
}

impl Voice for Sampler {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = ctx.rate as f64;
        let p = |i| param("sampler", params, i);
        let note = event.note as f64 + p(TUNE);
        let vel = event.vel.clamp(0.0, 1.0);
        let zone = ctx
            .bank
            .pick(self.library, note, (vel * 127.0).round() as u8);
        let step = zone
            .and_then(|z| ctx.bank.zone(self.library, z))
            .map_or(0.0, |z| z.rate / rate * ((note - z.root) / 12.0).exp2());
        *self = Sampler {
            library: self.library,
            zone,
            active: zone.is_some(),
            n: 0,
            gate: Gate::new(event, p(ATTACK), p(RELEASE), rate),
            vel: vel as f64,
            step,
        };
    }

    fn render(&mut self, out: &mut [[f32; 2]], ctx: &Ctx) -> bool {
        if !self.active {
            return false;
        }
        // the bank may have been cleared since the start
        let Some(zone) = self.zone.and_then(|z| ctx.bank.zone(self.library, z)) else {
            self.active = false;
            return false;
        };
        let last = (zone.frames() - 1) as f64;
        for frame in out.iter_mut() {
            // from the frame count, not a running sum, so the place in the zone is exact
            let pos = self.n as f64 * self.step;
            if self.gate.done(self.n) || pos > last {
                self.active = false;
                return false;
            }
            let g = self.vel * self.gate.at(self.n);
            frame[0] += (Self::read(zone, pos, 0) * g) as f32;
            frame[1] += (Self::read(zone, pos, 1) * g) as f32;
            self.n += 1;
        }
        self.active = !self.gate.done(self.n) && self.n as f64 * self.step <= last;
        self.active
    }
}

/// One recorded hit on a drum row: its zone at its own pitch, moved by the step's pitch and
/// `tune`, cut after `length` (times the step's decay scale) or at the recording's end.
#[derive(Default)]
struct OneShot {
    rate: f64,
    library: usize,
    zone: Option<usize>,
    active: bool,
    gate: HitGate,
    step: f64,
    attack: f64,
}

mod oneshot {
    pub const TUNE: usize = 0;
    pub const LENGTH: usize = 1;
    pub const ATTACK: usize = 2;
}

impl Voice for OneShot {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = self.rate;
        let p = |i| param("oneshot", params, i);
        // a drum hit's note is 60 plus the step's pitch
        let shift = event.note as f64 - 60.0 + p(oneshot::TUNE);
        let vel = (event.vel.clamp(0.0, 1.0) * 127.0).round() as u8;
        let zone = ctx.bank.pick(self.library, 60.0, vel);
        let found = zone.and_then(|z| ctx.bank.zone(self.library, z));
        let step = found.map_or(0.0, |z| z.rate / rate * (shift / 12.0).exp2());
        let recorded = found.map_or(0, |z| (z.frames() as f64 / step).floor() as u64);
        let cut = (p(oneshot::LENGTH) * (event.decay as f64).max(0.0) * rate).round() as u64;
        *self = OneShot {
            rate,
            library: self.library,
            zone,
            active: zone.is_some(),
            gate: HitGate::new(event, rate, Some(recorded.min(cut))),
            step,
            attack: (p(oneshot::ATTACK) * rate).max(1.0),
        };
    }

    fn render(&mut self, out: &mut [[f32; 2]], ctx: &Ctx) -> bool {
        if !self.active {
            return false;
        }
        let Some(zone) = self.zone.and_then(|z| ctx.bank.zone(self.library, z)) else {
            self.active = false;
            return false;
        };
        let last = (zone.frames() - 1) as f64;
        for frame in out.iter_mut() {
            let Some((k, g)) = self.gate.next() else {
                self.active = false;
                return false;
            };
            let pos = k as f64 * self.step;
            if pos > last {
                self.active = false;
                return false;
            }
            let g = g * (k as f64 / self.attack).min(1.0);
            frame[0] += (Sampler::read(zone, pos, 0) * g) as f32;
            frame[1] += (Sampler::read(zone, pos, 1) * g) as f32;
        }
        true
    }
}

mod sample808 {
    pub const TUNE: usize = 0;
    pub const DECAY: usize = 1;
    pub const RELEASE: usize = 2;
    pub const GLIDE_MS: usize = 3;
}

/// How close a glide gets to its note by `glide_ms`, in semitones: one cent.
const GLIDE_REACH: f64 = 0.01;
/// A legato note starts this far into the recording, past its attack, so the slide does
/// not hit again.
const LEGATO_SKIP_S: f64 = 0.05;

/// A recorded 808 played as a mono bass: the zone nearest the note, pitched to it, gliding in
/// from the note it cut, held with a decay and released after the note's end.
#[derive(Default)]
struct Sample808 {
    rate: f64,
    library: usize,
    zone: Option<usize>,
    active: bool,
    gate: HitGate,
    /// zone frames a render frame at the note itself
    step: f64,
    pos: f64,
    away: f64,
    glide: f64,
    held: Fall,
    off: Option<u64>,
    release_s: f64,
    release: Fall,
    level: f64,
    released: bool,
}

impl Voice for Sample808 {
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx) {
        let rate = self.rate;
        let p = |i| param("sample808", params, i);
        let note = event.note as f64 + p(sample808::TUNE);
        let vel = (event.vel.clamp(0.0, 1.0) * 127.0).round() as u8;
        let zone = ctx.bank.pick(self.library, note, vel);
        let found = zone.and_then(|z| ctx.bank.zone(self.library, z));
        let step = found.map_or(0.0, |z| z.rate / rate * ((note - z.root) / 12.0).exp2());
        let away = event
            .legato_from
            .map_or(0.0, |from| (from - event.note) as f64);
        let frames = (p(sample808::GLIDE_MS) / 1000.0 * rate).max(1.0);
        let glide = if away.abs() > GLIDE_REACH {
            (-(away.abs() / GLIDE_REACH).ln() / frames).exp()
        } else {
            0.0
        };
        let off = event.off.map(|o| o.saturating_sub(event.start));
        let release_s = p(sample808::RELEASE);
        let life = off.map(|o| o + (release_s * rate).ceil() as u64 + 1);
        let skip = if event.legato_from.is_some() {
            found.map_or(0.0, |z| LEGATO_SKIP_S * z.rate)
        } else {
            0.0
        };
        *self = Sample808 {
            rate,
            library: self.library,
            zone,
            active: zone.is_some(),
            gate: HitGate::new(event, rate, life),
            step,
            pos: skip,
            away,
            glide,
            held: Fall::new(p(sample808::DECAY) * (event.decay as f64).max(0.0), rate),
            off,
            release_s,
            release: Fall::default(),
            level: 0.0,
            released: false,
        };
    }

    fn render(&mut self, out: &mut [[f32; 2]], ctx: &Ctx) -> bool {
        if !self.active {
            return false;
        }
        let Some(zone) = self.zone.and_then(|z| ctx.bank.zone(self.library, z)) else {
            self.active = false;
            return false;
        };
        let last = (zone.frames() - 1) as f64;
        for frame in out.iter_mut() {
            let Some((k, g)) = self.gate.next() else {
                self.active = false;
                return false;
            };
            if self.pos > last {
                self.active = false;
                return false;
            }
            if !self.released && self.off.is_some_and(|off| k >= off) {
                self.released = true;
                self.level = self.held.next();
                self.release = Fall::new(self.release_s, self.rate);
            }
            let env = if self.released {
                self.level * self.release.next()
            } else {
                self.held.next()
            };
            let g = g * env;
            frame[0] += (Sampler::read(zone, self.pos, 0) * g) as f32;
            frame[1] += (Sampler::read(zone, self.pos, 1) * g) as f32;
            // the glide bends the reading speed, so the place is a running sum
            self.pos += self.step * (self.away / 12.0).exp2();
            self.away *= self.glide;
        }
        true
    }
}

/// A sampler voice that plays the bank's library of that index; an unknown or empty one
/// plays silence.
pub fn sampler_for(_rate: f32, library: usize) -> Box<dyn Voice> {
    Box::new(Sampler {
        library,
        ..Sampler::default()
    })
}

/// A voice of a sampled instrument (`sampler`, `oneshot`, `sample808`) that plays the bank's
/// library of that index.
pub fn sampled_for(instrument: &str, rate: f32, library: usize) -> Box<dyn Voice> {
    match instrument {
        "oneshot" | "oneshot_hat" | "oneshot_open_hat" => Box::new(OneShot {
            rate: rate as f64,
            library,
            ..OneShot::default()
        }),
        "sample808" => Box::new(Sample808 {
            rate: rate as f64,
            library,
            ..Sample808::default()
        }),
        _ => sampler_for(rate, library),
    }
}

pub fn make(instrument: &str, rate: f32) -> Option<Box<dyn Voice>> {
    SAMPLED
        .contains(&instrument)
        .then(|| sampled_for(instrument, rate, 0))
}
