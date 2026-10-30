//! The rack of effects and the beat player behind a C ABI, for the studio pages' AudioWorklets
//! through emscripten.
//!
//! A rack or a player is made from JSON and named by a handle; a handle that was never given
//! or has been freed is refused, not followed. Samples are interleaved stereo f32 in the
//! caller's memory, processed in place in the output, so nothing is copied into buffers of
//! ours and a block may be of any length. After `rack_new`, `rack_process` and a `rack_set`
//! that succeeds do not allocate; nor do `beat_process` and `beat_seek`. A failing call
//! returns 0, -1 or a null pointer and leaves its reason for `rack_error`, the one error
//! getter of the module. A panic does not cross the boundary: it fails the call, and a rack
//! or player that panicked is freed, as its state can no longer be trusted.
//!
//! Frame counts of a song (`beat_seek`, `beat_length`, `beat_position`) cross as f64: a JS
//! number holds every whole frame up to 2^53 exactly, where a u64 would need BigInt.

use redline_dsp_core::beat::events::EventTable;
use redline_dsp_core::beat::instruments::instruments_json;
use redline_dsp_core::beat::player::Player;
use redline_dsp_core::beat::score::{Score, MAX_RACK};
use redline_dsp_core::beat::voices::SampleBank;
use redline_dsp_core::effect::{effects_json as list_effects, make, Rack};
use redline_dsp_core::filters;
use std::cell::{OnceCell, RefCell};
use std::ffi::{c_char, CString};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::thread::LocalKey;

struct Slot<T> {
    generation: u16,
    item: Option<Box<T>>,
}

type Table<T> = RefCell<Vec<Slot<T>>>;

thread_local! {
    static RACKS: Table<Rack> = const { RefCell::new(Vec::new()) };
    static BEATS: Table<Player> = const { RefCell::new(Vec::new()) };
    static ERROR: RefCell<CString> = RefCell::new(CString::default());
    static EFFECTS: OnceCell<CString> = const { OnceCell::new() };
    static INSTRUMENTS: OnceCell<CString> = const { OnceCell::new() };
    /// The JSON the last `beat_events_json` or `beat_check_json` gave.
    static RESULT: RefCell<CString> = RefCell::new(CString::default());
}

/// The most racks, or players, alive at once: a handle keeps the slot in its low 16 bits.
const MAX_HANDLES: usize = 0xffff;
/// The largest frame a JS number holds exactly.
const MAX_FRAME: f64 = 9_007_199_254_740_992.0;

fn fail(message: impl Into<String>) {
    let mut bytes = message.into().into_bytes();
    bytes.retain(|&b| b != 0);
    let message = CString::new(bytes).unwrap_or_default();
    ERROR.with_borrow_mut(|e| *e = message);
}

fn panic_message(what: &str, payload: &(dyn std::any::Any + Send)) -> String {
    let message = payload
        .downcast_ref::<&str>()
        .copied()
        .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
        .unwrap_or("no message");
    format!("the {what} panicked: {message}")
}

fn handle_of(index: usize, generation: u16) -> u32 {
    (u32::from(generation) << 16) | (index as u32 + 1)
}

/// Keeps `item` in a free slot of the table; its handle, or 0.
fn insert<T>(table: &'static LocalKey<Table<T>>, item: Box<T>, what: &str) -> u32 {
    table.with_borrow_mut(|slots| {
        let index = match slots.iter().position(|s| s.item.is_none()) {
            Some(i) => i,
            None if slots.len() < MAX_HANDLES => {
                slots.push(Slot {
                    generation: 0,
                    item: None,
                });
                slots.len() - 1
            }
            None => {
                fail(format!("at most {MAX_HANDLES} {what} are alive at once"));
                return 0;
            }
        };
        let slot = &mut slots[index];
        slot.item = Some(item);
        handle_of(index, slot.generation)
    })
}

/// The slot a handle names, if its item is alive.
fn with_item<T, R>(
    table: &'static LocalKey<Table<T>>,
    handle: u32,
    f: impl FnOnce(&mut Slot<T>) -> R,
) -> Option<R> {
    let index = (handle & 0xffff) as usize;
    let generation = (handle >> 16) as u16;
    table.with_borrow_mut(|slots| {
        let slot = slots.get_mut(index.checked_sub(1)?)?;
        if slot.generation != generation || slot.item.is_none() {
            return None;
        }
        Some(f(slot))
    })
}

/// Empties the slot; its next item gets a handle this one does not match.
fn vacate<T>(slot: &mut Slot<T>) -> Option<Box<T>> {
    slot.generation = slot.generation.wrapping_add(1);
    slot.item.take()
}

fn drop_quietly<T>(item: Box<T>) {
    let _ = catch_unwind(AssertUnwindSafe(|| drop(item)));
}

fn code(done: Option<Result<(), String>>, missing: impl FnOnce()) -> i32 {
    match done {
        Some(Ok(())) => 0,
        Some(Err(message)) => {
            fail(message);
            -1
        }
        None => {
            missing();
            -1
        }
    }
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
    match made {
        Ok(Ok(rack)) => insert(&RACKS, rack, "racks"),
        Ok(Err(message)) => {
            fail(message);
            0
        }
        Err(payload) => {
            fail(panic_message("rack", payload.as_ref()));
            0
        }
    }
}

/// Frees a rack; a handle that names none is let be.
#[no_mangle]
pub extern "C" fn rack_free(handle: u32) {
    if let Some(rack) = with_item(&RACKS, handle, vacate).flatten() {
        drop_quietly(rack);
    }
}

/// Frames from an input to the output it shows up in, or -1.
#[no_mangle]
pub extern "C" fn rack_latency(handle: u32) -> i32 {
    match with_item(&RACKS, handle, |slot| {
        slot.item.as_ref().map(|r| r.latency())
    })
    .flatten()
    {
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
    let ran = with_item(&RACKS, handle, |slot| {
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
            .item
            .as_mut()
            .expect("with_item hands over a live rack");
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
            if let Some(rack) = vacate(slot) {
                drop_quietly(rack);
            }
            format!(
                "{}; the rack is freed",
                panic_message("rack", payload.as_ref())
            )
        })
    });
    code(ran, || no_rack(handle))
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
    let done = with_item(&RACKS, handle, |s| {
        let name = unsafe { text(name, len, "the param's name") }?;
        let rack = s.item.as_mut().expect("with_item hands over a live rack");
        catch_unwind(AssertUnwindSafe(|| rack.set(slot as usize, name, value)))
            .map_err(|payload| panic_message("rack", payload.as_ref()))?
            .map_err(|e| e.to_string())
    });
    code(done, || no_rack(handle))
}

/// Clears what a rack has heard (delay lines, tails, followers) for `rate` Hz and keeps its
/// params: 0, or -1. It may allocate; call it where a gap in the sound does not matter.
#[no_mangle]
pub extern "C" fn rack_reset(handle: u32, rate: f32) -> i32 {
    let done = with_item(&RACKS, handle, |s| {
        let rack = s.item.as_mut().expect("with_item hands over a live rack");
        catch_unwind(AssertUnwindSafe(|| rack.reset(rate)))
            .map_err(|payload| panic_message("rack", payload.as_ref()))?
            .map_err(|e| e.to_string())
    });
    code(done, || no_rack(handle))
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

fn no_beat(handle: u32) {
    fail(format!("no beat has the handle {handle}"));
}

/// Runs `f` on the player a handle names; a panic fails the call and frees the player.
fn on_beat<R>(
    handle: u32,
    f: impl FnOnce(&mut Player) -> Result<R, String>,
) -> Option<Result<R, String>> {
    with_item(&BEATS, handle, |slot| {
        let player = slot
            .item
            .as_mut()
            .expect("with_item hands over a live player");
        catch_unwind(AssertUnwindSafe(|| f(player))).unwrap_or_else(|payload| {
            let message = panic_message("beat player", payload.as_ref());
            if let Some(player) = vacate(slot) {
                drop_quietly(player);
            }
            Err(format!("{message}; the player is freed"))
        })
    })
}

fn beat_code(handle: u32, done: Option<Result<(), String>>) -> i32 {
    code(done, || no_beat(handle))
}

/// The score, or every error as a "path: message" line.
fn score_of(text: &str) -> Result<Score, String> {
    Score::from_json(text).map_err(|errors| {
        let lines: Vec<String> = errors.iter().map(ToString::to_string).collect();
        lines.join("\n")
    })
}

/// A sound's rack as the score writes it, a list of effects, or as `Rack::from_json` reads
/// it; refused as the score's racks are: too many effects or an offline-only one.
fn sound_rack(json: &str, rate: f32) -> Result<Rack, String> {
    let rack = if json.trim_start().starts_with('[') {
        Rack::from_json(&format!("{{\"effects\": {json}}}"))
    } else {
        Rack::from_json(json)
    };
    let mut rack = rack.map_err(|e| e.to_string())?;
    if rack.len() > MAX_RACK {
        return Err(format!(
            "{} effects, a rack takes at most {MAX_RACK}",
            rack.len()
        ));
    }
    for slot in 0..rack.len() {
        let name = rack.effect_name(slot).unwrap_or_default();
        if make(name).is_some_and(|e| e.offline_only()) {
            return Err(format!(
                "{name} works only offline; a beat plays in real time"
            ));
        }
    }
    rack.reset(rate).map_err(|e| e.to_string())?;
    Ok(rack)
}

fn json_string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn give(json: String) -> *const c_char {
    let json = CString::new(json).unwrap_or_default();
    RESULT.with_borrow_mut(|r| {
        *r = json;
        r.as_ptr()
    })
}

/// Makes a player of the score's JSON at `rate` Hz with an empty sample bank; its handle,
/// or 0 with every error of the score as a "path: message" line.
///
/// # Safety
/// `score` is null or points to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn beat_new(score: *const u8, len: usize, rate: f32) -> u32 {
    let made = catch_unwind(|| -> Result<Box<Player>, String> {
        let score = score_of(unsafe { text(score, len, "the score's JSON") }?)?;
        Player::new(&score, rate, SampleBank::default()).map(Box::new)
    });
    match made {
        Ok(Ok(player)) => insert(&BEATS, player, "beat players"),
        Ok(Err(message)) => {
            fail(message);
            0
        }
        Err(payload) => {
            fail(panic_message("beat player", payload.as_ref()));
            0
        }
    }
}

/// Frees a player; a handle that names none is let be.
#[no_mangle]
pub extern "C" fn beat_free(handle: u32) {
    if let Some(player) = with_item(&BEATS, handle, vacate).flatten() {
        drop_quietly(player);
    }
}

/// Takes a new score with the same sounds from the next block on: 0, or -1. A score with
/// other sounds fails with a message starting "rebuild": make a new player for it.
///
/// # Safety
/// `score` is null or points to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn beat_set_score(handle: u32, score: *const u8, len: usize) -> i32 {
    let done = on_beat(handle, |player| {
        let score = score_of(unsafe { text(score, len, "the score's JSON") }?)?;
        player.set_score(&score)
    });
    beat_code(handle, done)
}

/// Builds the rack (the score's list of effects, or `{"effects": [...]}`) at the player's
/// rate and crossfades the sound over to it: 0, or -1. A rack later than the player
/// compensates fails with "rebuild". Allocates; the rack it retires is dropped here.
///
/// # Safety
/// `json` is null or points to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn beat_set_rack(
    handle: u32,
    sound: u32,
    json: *const u8,
    len: usize,
) -> i32 {
    let done = on_beat(handle, |player| {
        let rack = sound_rack(
            unsafe { text(json, len, "the rack's JSON") }?,
            player.rate(),
        )?;
        drop(player.set_rack(sound as usize, rack)?);
        Ok(())
    });
    beat_code(handle, done)
}

/// Plays on from the song's `frame`, a whole number from 0 to 2^53: 0, or -1.
#[no_mangle]
pub extern "C" fn beat_seek(handle: u32, frame: f64) -> i32 {
    let done = on_beat(handle, |player| {
        if !((0.0..=MAX_FRAME).contains(&frame) && frame.fract() == 0.0) {
            return Err(format!(
                "the frame {frame} is not a whole number from 0 to 2^53"
            ));
        }
        player.seek(frame as u64);
        Ok(())
    });
    beat_code(handle, done)
}

/// Overwrites `output` with the next `frames` interleaved stereo frames of the mix: 0, or -1.
///
/// # Safety
/// `output` points to `2 * frames` writable f32, aligned to 4 bytes, unless `frames` is 0.
#[no_mangle]
pub unsafe extern "C" fn beat_process(handle: u32, output: *mut f32, frames: usize) -> i32 {
    let done = on_beat(handle, |player| {
        if frames == 0 {
            return Ok(());
        }
        if frames > isize::MAX as usize / 8 {
            return Err(format!("{frames} frames do not fit in memory"));
        }
        if output.is_null() || !output.cast::<[f32; 2]>().is_aligned() {
            return Err("the output must be an aligned pointer, not null".to_string());
        }
        let block = unsafe { std::slice::from_raw_parts_mut(output.cast::<[f32; 2]>(), frames) };
        player.process(block);
        Ok(())
    });
    beat_code(handle, done)
}

/// Frames from a frame of the song to the mix's frame that carries it, or -1.
#[no_mangle]
pub extern "C" fn beat_latency(handle: u32) -> i32 {
    let done = on_beat(handle, |player| {
        i32::try_from(player.latency())
            .map_err(|_| format!("the latency {} does not fit an i32", player.latency()))
    });
    match done {
        Some(Ok(latency)) => latency,
        Some(Err(message)) => {
            fail(message);
            -1
        }
        None => {
            no_beat(handle);
            -1
        }
    }
}

/// Frames of the song without its tails, or -1.
#[no_mangle]
pub extern "C" fn beat_length(handle: u32) -> f64 {
    match on_beat(handle, |player| Ok(player.table().length as f64)) {
        Some(Ok(length)) => length,
        _ => {
            no_beat(handle);
            -1.0
        }
    }
}

/// The song frame the next block starts at, or -1.
#[no_mangle]
pub extern "C" fn beat_position(handle: u32) -> f64 {
    match on_beat(handle, |player| Ok(player.position() as f64)) {
        Some(Ok(position)) => position,
        _ => {
            no_beat(handle);
            -1.0
        }
    }
}

/// Adds a recorded zone to the player's sample bank: `frames` frames of i16 samples,
/// interleaved when `channels` is 2, at `rate` Hz, for MIDI velocities `vel_lo..=vel_hi`
/// around `root`. 0, or -1; past the bank's budget it fails with the bank's reason. Then
/// `beat_rebuild_samplers` lets the samplers find it. Allocates.
///
/// # Safety
/// `library` is null or points to `library_len` readable bytes; `samples` points to
/// `frames * channels` readable i16, aligned to 2 bytes, unless `frames` is 0.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn beat_load_zone(
    handle: u32,
    library: *const u8,
    library_len: usize,
    root: u32,
    vel_lo: u32,
    vel_hi: u32,
    rate: f32,
    samples: *const i16,
    frames: usize,
    channels: u32,
) -> i32 {
    let done = on_beat(handle, |player| {
        let library = unsafe { text(library, library_len, "the library's name") }?;
        let midi = |v: u32, what: &str| {
            u8::try_from(v).map_err(|_| format!("{what} {v} is not within 0..127"))
        };
        let root = midi(root, "root note")?;
        let vel = midi(vel_lo, "velocity")?..=midi(vel_hi, "velocity")?;
        if !(1..=2).contains(&channels) {
            return Err(format!("{channels} channels; a zone is mono or stereo"));
        }
        let channels = channels as usize;
        let bank = player.bank_mut();
        let n = frames
            .checked_mul(channels)
            .filter(|&n| n <= isize::MAX as usize / 2)
            .ok_or_else(|| format!("{frames} frames do not fit in memory"))?;
        // refused before the samples are looked at, as the bank would
        if bank.used() + n > bank.budget() {
            return Err(format!(
                "the zone needs {n} samples, but {} of the {} sample budget are used",
                bank.used(),
                bank.budget()
            ));
        }
        let samples: &[i16] = if n == 0 {
            &[]
        } else if samples.is_null() || !samples.is_aligned() {
            return Err("the samples must be an aligned pointer, not null".to_string());
        } else {
            unsafe { std::slice::from_raw_parts(samples, n) }
        };
        bank.load(library, root, vel, rate, samples, channels)
    });
    beat_code(handle, done)
}

/// Makes the player's sampler voices anew for the bank's libraries: 0, or -1. Allocates
/// and silences ringing samplers: call it between blocks, after loading zones.
#[no_mangle]
pub extern "C" fn beat_rebuild_samplers(handle: u32) -> i32 {
    let done = on_beat(handle, |player| player.rebuild_samplers());
    beat_code(handle, done)
}

/// The score's event table at `rate` Hz as JSON (see `EventTable::to_json`), NUL-terminated
/// and valid until the next `beat_events_json` or `beat_check_json`; null with every error
/// of the score as a "path: message" line.
///
/// # Safety
/// `score` is null or points to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn beat_events_json(
    score: *const u8,
    len: usize,
    rate: f32,
) -> *const c_char {
    let made = catch_unwind(|| -> Result<String, String> {
        if !(rate.is_finite() && rate > 0.0) {
            return Err(format!("rate {rate} is not a positive number"));
        }
        let score = score_of(unsafe { text(score, len, "the score's JSON") }?)?;
        Ok(EventTable::build(&score, rate).to_json(&score))
    });
    match made {
        Ok(Ok(json)) => give(json),
        Ok(Err(message)) => {
            fail(message);
            std::ptr::null()
        }
        Err(payload) => {
            fail(panic_message("event table", payload.as_ref()));
            std::ptr::null()
        }
    }
}

/// Every error of the score as a JSON list of "path: message", empty when it is valid;
/// valid until the next `beat_events_json` or `beat_check_json`. Null only when the text
/// itself cannot be read.
///
/// # Safety
/// `score` is null or points to `len` readable bytes.
#[no_mangle]
pub unsafe extern "C" fn beat_check_json(score: *const u8, len: usize) -> *const c_char {
    let made = catch_unwind(|| -> Result<String, String> {
        let text = unsafe { text(score, len, "the score's JSON") }?;
        let errors = Score::from_json(text).err().unwrap_or_default();
        let mut json = String::from("[");
        for (i, e) in errors.iter().enumerate() {
            if i > 0 {
                json.push_str(", ");
            }
            json_string(&e.to_string(), &mut json);
        }
        json.push(']');
        Ok(json)
    });
    match made {
        Ok(Ok(json)) => give(json),
        Ok(Err(message)) => {
            fail(message);
            std::ptr::null()
        }
        Err(payload) => {
            fail(panic_message("score check", payload.as_ref()));
            std::ptr::null()
        }
    }
}

/// Every beat instrument with its params, as `instruments_json` in the core: NUL-terminated
/// JSON that lives as long as the module.
#[no_mangle]
pub extern "C" fn beat_instruments_json() -> *const c_char {
    INSTRUMENTS.with(|i| {
        i.get_or_init(|| CString::new(instruments_json()).unwrap_or_default())
            .as_ptr()
    })
}
