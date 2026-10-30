//! Safe Rust over signalsmith-stretch (vendored C++, MIT): pitch and formant shifting of
//! interleaved samples. Creating and configuring allocate; processing does not.

use std::ffi::{c_int, c_long, c_void};
use std::ptr::NonNull;

extern "C" {
    fn rl_stretch_create(seed: c_long) -> *mut c_void;
    fn rl_stretch_destroy(h: *mut c_void);
    fn rl_stretch_configure(
        h: *mut c_void,
        channels: c_int,
        block: c_int,
        interval: c_int,
    ) -> c_int;
    fn rl_stretch_set_transpose(h: *mut c_void, factor: f32);
    fn rl_stretch_set_formant(h: *mut c_void, factor: f32, compensate: c_int, base: f32);
    fn rl_stretch_process(
        h: *mut c_void,
        input: *const f32,
        input_frames: c_int,
        output: *mut f32,
        output_frames: c_int,
    );
    fn rl_stretch_reset(h: *mut c_void);
    fn rl_stretch_input_latency(h: *const c_void) -> c_int;
    fn rl_stretch_output_latency(h: *const c_void) -> c_int;
}

/// One stretcher of a fixed channel count at a fixed rate.
pub struct Stretch {
    handle: NonNull<c_void>,
    channels: usize,
}

// The C++ object is owned by this value alone and has no thread affinity.
unsafe impl Send for Stretch {}

impl Stretch {
    /// The library's default preset: 120 ms blocks every 30 ms. The seed fixes the phase
    /// randomisation the library uses for large time stretches, so output is deterministic.
    pub fn new(channels: usize, rate: f32, seed: u32) -> Stretch {
        Self::with_blocks(
            channels,
            (rate * 0.12) as usize,
            (rate * 0.03) as usize,
            seed,
        )
    }

    pub fn with_blocks(channels: usize, block: usize, interval: usize, seed: u32) -> Stretch {
        assert!(channels > 0 && block > 0 && interval > 0 && interval <= block);
        let raw = unsafe { rl_stretch_create(seed as c_long) };
        let handle = NonNull::new(raw).expect("signalsmith-stretch: out of memory");
        let s = Stretch { handle, channels };
        let failed = unsafe {
            rl_stretch_configure(
                s.handle.as_ptr(),
                channels as c_int,
                block as c_int,
                interval as c_int,
            )
        };
        assert_eq!(failed, 0, "signalsmith-stretch: out of memory");
        s
    }

    pub fn channels(&self) -> usize {
        self.channels
    }

    /// Frequencies are multiplied by `factor` from the next spectral block on.
    pub fn set_transpose(&mut self, factor: f32) {
        unsafe { rl_stretch_set_transpose(self.handle.as_ptr(), factor) }
    }

    /// Formants multiplied by `factor`; with `compensate` they also stay where they were
    /// under a transpose. `base` is a rough fundamental in Hz over the rate, 0 to detect it.
    pub fn set_formant(&mut self, factor: f32, compensate: bool, base: f32) {
        unsafe {
            rl_stretch_set_formant(self.handle.as_ptr(), factor, c_int::from(compensate), base)
        }
    }

    /// Interleaved frames in, interleaved frames out; unequal lengths stretch time.
    pub fn process(&mut self, input: &[f32], output: &mut [f32]) {
        let ch = self.channels;
        assert!(input.len().is_multiple_of(ch) && output.len().is_multiple_of(ch));
        unsafe {
            rl_stretch_process(
                self.handle.as_ptr(),
                input.as_ptr(),
                (input.len() / ch) as c_int,
                output.as_mut_ptr(),
                (output.len() / ch) as c_int,
            )
        }
    }

    pub fn reset(&mut self) {
        unsafe { rl_stretch_reset(self.handle.as_ptr()) }
    }

    /// How far behind the newest input the analysed moment lies.
    pub fn input_latency(&self) -> usize {
        unsafe { rl_stretch_input_latency(self.handle.as_ptr()) as usize }
    }

    /// Input to output, in frames, for a pitch shift.
    pub fn latency(&self) -> usize {
        self.input_latency() + unsafe { rl_stretch_output_latency(self.handle.as_ptr()) as usize }
    }
}

impl Drop for Stretch {
    fn drop(&mut self) {
        unsafe { rl_stretch_destroy(self.handle.as_ptr()) }
    }
}
