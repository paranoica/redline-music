//! The voices that play events. A voice's output is a function of its event, its sound's
//! params and the frames since the event began, nothing else: a render started anywhere
//! in the song matches the whole render there.

use super::events::{hash, Event};

mod bass;
mod drums;
mod fm;
mod pluck;
mod saw;
mod sampler;

pub use sampler::SampleBank;

/// What every voice reads besides its event.
pub struct Ctx<'a> {
    pub rate: f32,
    pub bank: &'a SampleBank,
}

pub trait Voice: Send {
    /// Begin `event` at its first frame, with the sound's params in the instrument's order.
    /// Must not allocate: whatever the voice needs is sized in its constructor.
    fn start(&mut self, event: &Event, params: &[f32], ctx: &Ctx);
    /// Add the next `out.len()` frames into `out`. False once the voice is silent for good.
    fn render(&mut self, out: &mut [[f32; 2]], ctx: &Ctx) -> bool;
}

/// A voice of the named instrument at the rate, or None for an unknown name.
pub fn make(instrument: &str, rate: f32) -> Option<Box<dyn Voice>> {
    drums::make(instrument, rate)
        .or_else(|| bass::make(instrument, rate))
        .or_else(|| fm::make(instrument, rate))
        .or_else(|| pluck::make(instrument, rate))
        .or_else(|| saw::make(instrument, rate))
        .or_else(|| sampler::make(instrument, rate))
}

/// The pitch of a MIDI note in Hz.
pub fn hz(note: f64) -> f64 {
    440.0 * ((note - 69.0) / 12.0).exp2()
}

/// White noise in -1..1 for frame `n` of event `id`, drawn from a hash so it does not
/// depend on where a render began.
pub fn noise(id: u32, n: u64) -> f32 {
    let h = hash(&[u64::from(id), n]);
    ((h >> 40) as f32 / (1u64 << 23) as f32) - 1.0
}
