//! The rack of effects behind a C ABI, for the studio page's AudioWorklet through emscripten.
//!
//! A rack is made from JSON and named by a handle; a handle that was never given or has been
//! freed is refused, not followed. Samples are interleaved stereo f32 in the caller's memory,
//! processed in place in the output, so nothing is copied into buffers of ours and a block may
//! be of any length. After `rack_new`, `rack_process` and a `rack_set` that succeeds do not
//! allocate. A failing call returns 0 or -1 and leaves its reason for `rack_error`. A panic
//! does not cross the boundary: it fails the call, and a rack that panicked while processing
//! is freed, as its state can no longer be trusted.

use redline_dsp_core::effect::{effects_json as list_effects, Rack};
use redline_dsp_core::filters;
use std::cell::{OnceCell, RefCell};
use std::ffi::{c_char, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};

struct Slot {
    generation: u16,
    rack: Option<Box<Rack>>,
}

thread_local! {
    static RACKS: RefCell<Vec<Slot>> = const { RefCell::new(Vec::new()) };
    static ERROR: RefCell<CString> = RefCell::new(CString::default());
    static EFFECTS: OnceCell<CString> = const { OnceCell::new() };
}

/// The most racks alive at once: a handle keeps the slot in its low 16 bits.
const MAX_RACKS: usize = 0xffff;

fn fail(message: impl Into<String>) {
    let mut bytes = message.into().into_bytes();
    bytes.retain(|&b| b != 0);
    let message = CString::new(bytes).unwrap_or_default();
    ERROR.with_borrow_mut(|e| *e = message);
}

fn panic_message(payload: &(dyn std::any::Any + Send)) -> String {
    let what = payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("no message");
    format!("the rack panicked: {what}")
}

fn handle_of(index: usize, generation: u16) -> u32 {
    (u32::from(generation) << 16) | (index as u32 + 1)
}

/// The rack a handle names, if it is alive.
fn with_rack<R>(handle: u32, f: impl FnOnce(&mut Slot) -> R) -> Option<R> {
    let index = (handle & 0xffff) as usize;
    let generation = (handle >> 16) as u16;
    RACKS.with_borrow_mut(|racks| {
        let slot = racks.get_mut(index.checked_sub(1)?)?;
        if slot.generation != generation || slot.rack.is_none() {
            return None;
        }
        Some(f(slot))
    })
}

fn no_rack(handle: u32) {
    fail(format!("no rack has the handle {handle}"));
}

/// The UTF-8 text at `ptr`, or why there is none.
///
/// # Safety
/// `ptr` is null or points to `len` readable bytes.
unsafe fn text<'a>(ptr: *const u8, len: usize, what: &str) -> Result<&'a str, String> {
    if ptr.is_null() {
        return Err(format!("{what} is a null pointer"));
    }
    let bytes = unsafe { std::slice::from_raw_parts(ptr, len) };
    std::str::from_utf8(bytes).map_err(|e| format!("{what} is not UTF-8: {e}"))
}

/// Makes a rack for `rate` Hz from the JSON `Rack::from_json` reads; its handle, or 0.
///
/// # Safety
/// `json` is null or points to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn rack_new(rate: f32, json: *const u8, len: usize) -> u32 {
    let made = catch_unwind(|| -> Result<Box<Rack>, String> {
        let json = unsafe { text(json, len, "the rack's JSON") }?;
        let mut rack = Rack::from_json(json).map_err(|e| e.to_string())?;
        rack.reset(rate).map_err(|e| e.to_string())?;
        Ok(Box::new(rack))
    });
    let rack = match made {
        Ok(Ok(rack)) => rack,
        Ok(Err(message)) => {
            fail(message);
            return 0;
        }
        Err(payload) => {
            fail(panic_message(payload.as_ref()));
            return 0;
        }
    };
    RACKS.with_borrow_mut(|racks| {
        let index = match racks.iter().position(|s| s.rack.is_none()) {
            Some(i) => i,
            None if racks.len() < MAX_RACKS => {
                racks.push(Slot {
                    generation: 0,
                    rack: None,
                });
                racks.len() - 1
            }
            None => {
                fail(format!("at most {MAX_RACKS} racks are alive at once"));
                return 0;
            }
        };
        let slot = &mut racks[index];
        slot.rack = Some(rack);
        handle_of(index, slot.generation)
    })
}

/// Frees a rack; a handle that names none is let be.
#[no_mangle]
pub extern "C" fn rack_free(handle: u32) {
    let rack = with_rack(handle, |slot| {
        // the slot's next rack gets a handle this one does not match
        slot.generation = slot.generation.wrapping_add(1);
        slot.rack.take()
    })
    .flatten();
    if let Some(rack) = rack {
        let _ = catch_unwind(AssertUnwindSafe(|| drop(rack)));
    }
}

/// Frames from an input to the output it shows up in, or -1.
#[no_mangle]
pub extern "C" fn rack_latency(handle: u32) -> i32 {
    match with_rack(handle, |slot| slot.rack.as_ref().map(|r| r.latency())).flatten() {
        Some(latency) => i32::try_from(latency).unwrap_or_else(|_| {
            fail(format!("the latency {latency} does not fit an i32"));
            -1
        }),
        None => {
            no_rack(handle);
            -1
        }
    }
}

/// `frames` interleaved stereo frames from `input` through the rack into `output`: 0, or -1.
/// `side`, null or as long as the input, is the side signal for the effects that listen to
/// one. `input` may be `output`; `side` may not overlap it.
///
/// # Safety
/// `input` and `output` (and `side` unless null) point to `2 * frames` f32 each, readable
/// (writable for `output`), unless `frames` is 0.
#[no_mangle]
pub unsafe extern "C" fn rack_process(
    handle: u32,
    input: *const f32,
    side: *const f32,
    output: *mut f32,
    frames: usize,
) -> i32 {
    let ran = with_rack(handle, |slot| {
        if frames == 0 {
            return Ok(());
        }
        if frames > isize::MAX as usize / 8 {
            return Err(format!("{frames} frames do not fit in memory"));
        }
        if input.is_null() || output.is_null() {
            return Err("the input and the output must not be null".to_string());
        }
        let samples = 2 * frames;
        let bytes = 4 * samples;
        let out_at = output as usize;
        let side_at = side as usize;
        if !side.is_null()
            && side_at < out_at.saturating_add(bytes)
            && out_at < side_at.saturating_add(bytes)
        {
            return Err("the side may not overlap the output".to_string());
        }
        let rack = slot
            .rack
            .as_mut()
            .expect("with_rack hands over a live rack");
        let processed = catch_unwind(AssertUnwindSafe(|| {
            let _ftz = filters::flush_tiny();
            let block = unsafe {
                std::ptr::copy(input, output, samples);
                std::slice::from_raw_parts_mut(output.cast::<[f32; 2]>(), frames)
            };
            if side.is_null() {
                rack.process(block);
            } else {
                let side = unsafe { std::slice::from_raw_parts(side.cast::<[f32; 2]>(), frames) };
                rack.process_with(block, side);
            }
        }));
        processed.map_err(|payload| {
            if let Some(rack) = slot.rack.take() {
                slot.generation = slot.generation.wrapping_add(1);
                let _ = catch_unwind(AssertUnwindSafe(|| drop(rack)));
            }
            format!("{}; the rack is freed", panic_message(payload.as_ref()))
        })
    });
    match ran {
        Some(Ok(())) => 0,
        Some(Err(message)) => {
            fail(message);
            -1
        }
        None => {
            no_rack(handle);
            -1
        }
    }
}

/// Sets a param of the effect in `slot` to `value`: 0, or -1.
///
/// # Safety
/// `name` is null or points to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn rack_set(
    handle: u32,
    slot: u32,
    name: *const u8,
    len: usize,
    value: f32,
) -> i32 {
    let done = with_rack(handle, |s| {
        let name = unsafe { text(name, len, "the param's name") }?;
        let rack = s.rack.as_mut().expect("with_rack hands over a live rack");
        catch_unwind(AssertUnwindSafe(|| rack.set(slot as usize, name, value)))
            .map_err(|payload| panic_message(payload.as_ref()))?
            .map_err(|e| e.to_string())
    });
    match done {
        Some(Ok(())) => 0,
        Some(Err(message)) => {
            fail(message);
            -1
        }
        None => {
            no_rack(handle);
            -1
        }
    }
}

/// Clears what a rack has heard (delay lines, tails, followers) for `rate` Hz and keeps its
/// params: 0, or -1. It may allocate; call it where a gap in the sound does not matter.
#[no_mangle]
pub extern "C" fn rack_reset(handle: u32, rate: f32) -> i32 {
    let done = with_rack(handle, |s| {
        let rack = s.rack.as_mut().expect("with_rack hands over a live rack");
        catch_unwind(AssertUnwindSafe(|| rack.reset(rate)))
            .map_err(|payload| panic_message(payload.as_ref()))?
            .map_err(|e| e.to_string())
    });
    match done {
        Some(Ok(())) => 0,
        Some(Err(message)) => {
            fail(message);
            -1
        }
        None => {
            no_rack(handle);
            -1
        }
    }
}

/// Every effect with its params, as `effects_json` in the core: NUL-terminated JSON that
/// lives as long as the module.
#[no_mangle]
pub extern "C" fn effects_json() -> *const c_char {
    EFFECTS.with(|e| {
        e.get_or_init(|| CString::new(list_effects()).unwrap_or_default())
            .as_ptr()
    })
}

/// Why the last failing call failed, NUL-terminated; valid until the next failure.
#[no_mangle]
pub extern "C" fn rack_error() -> *const c_char {
    ERROR.with_borrow(|e| e.as_ptr())
}
