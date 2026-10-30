//! The recording engine without an audio API: a backend calls [`engine::Engine::cycle`] once a
//! period with the mic block and the output buffers; the monitor rack, the beat, the take
//! writer and the control side live here, so they can be tested with a fake device.

pub mod backend;
pub mod budget;
pub mod engine;
pub mod protocol;
pub mod ring;
pub mod wav;
