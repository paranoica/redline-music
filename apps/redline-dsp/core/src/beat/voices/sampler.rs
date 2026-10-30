//! Recorded zones and the voice that plays them.

use super::fm::{param, Gate};
use super::{Ctx, Voice};
use crate::beat::events::Event;
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

/// A sampler voice that plays the bank's library of that index; an unknown or empty one
/// plays silence.
pub fn sampler_for(_rate: f32, library: usize) -> Box<dyn Voice> {
    Box::new(Sampler {
        library,
        ..Sampler::default()
    })
}

pub fn make(instrument: &str, rate: f32) -> Option<Box<dyn Voice>> {
    (instrument == "sampler").then(|| sampler_for(rate, 0))
}
