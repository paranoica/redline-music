//! redline's DSP in plain Rust, with no Python and no threads: the offline loops the master
//! calls through the `redline_dsp` module, filter design, and a rack of real-time effects.

pub mod design;
pub mod effect;
mod effects;
pub mod filters;
pub mod loudness;
