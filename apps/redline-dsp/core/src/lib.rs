//! redline's DSP in Rust, with no Python and no threads: the offline loops the master
//! calls through the `redline_dsp` module, filter design, and a rack of real-time effects.
//! Pitch and formant shifting come from signalsmith-stretch, vendored C++ built by build.rs.

mod chain;
mod colour;
pub mod design;
mod dynamics;
pub mod effect;
mod effects;
mod faust;
pub mod filters;
pub mod loudness;
pub mod pitch;
mod space;
pub mod stretch;
mod time;
mod voice;
