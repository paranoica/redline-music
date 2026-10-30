use redline_dsp_core::effect::{self, Rack};
use redline_dsp_wasm::{
    effects_json, rack_error, rack_free, rack_latency, rack_new, rack_process, rack_set,
};
use std::ffi::CStr;
use std::ptr;

const RATE: f32 = 48000.0;
const GAIN: &str = r#"{"effects": [{"effect": "gain", "params": {"db": -6}}]}"#;
const PITCH: &str = r#"{"effects": [{"effect": "gain", "params": {"db": -6}}, {"effect": "pitch", "params": {"semitones": 12}}]}"#;
const DUCK: &str = r#"{"effects": [{"effect": "duck"}]}"#;

fn new_rack(rate: f32, json: &str) -> u32 {
    unsafe { rack_new(rate, json.as_ptr(), json.len()) }
}

fn error() -> String {
    unsafe { CStr::from_ptr(rack_error()) }
        .to_str()
        .unwrap()
        .to_string()
}

fn set(handle: u32, slot: u32, name: &str, value: f32) -> i32 {
    unsafe { rack_set(handle, slot, name.as_ptr(), name.len(), value) }
}

/// Interleaved stereo: a 375 Hz sine at half scale on the left, 625 Hz at a quarter on the
/// right, so a swapped or dropped channel shows.
fn sines(frames: usize) -> Vec<f32> {
    (0..frames)
        .flat_map(|n| {
            let t = n as f64 / RATE as f64;
            [
                (0.5 * (std::f64::consts::TAU * 375.0 * t).sin()) as f32,
                (0.25 * (std::f64::consts::TAU * 625.0 * t).sin()) as f32,
            ]
        })
        .collect()
}

fn process(handle: u32, input: &[f32], side: Option<&[f32]>, block: usize) -> Vec<f32> {
    let mut out = vec![0.0; input.len()];
    for (k, (i, o)) in input
        .chunks(2 * block)
        .zip(out.chunks_mut(2 * block))
        .enumerate()
    {
        let s = side.map_or(ptr::null(), |s| s[k * 2 * block..].as_ptr());
        let code = unsafe { rack_process(handle, i.as_ptr(), s, o.as_mut_ptr(), i.len() / 2) };
        assert_eq!(code, 0, "{}", error());
    }
    out
}

#[test]
fn gain_scales_by_its_decibels() {
    let h = new_rack(RATE, GAIN);
    assert_ne!(h, 0, "{}", error());
    assert_eq!(rack_latency(h), 0);
    let input = sines(4096);
    let out = process(h, &input, None, 128);
    let scale = 10f32.powf(-6.0 / 20.0);
    for (x, y) in input.iter().zip(&out) {
        assert!((y - x * scale).abs() < 1e-6, "{y} != {x} * {scale}");
    }
    rack_free(h);
}

#[test]
fn matches_the_core_rack_block_for_block() {
    let h = new_rack(RATE, PITCH);
    assert_ne!(h, 0, "{}", error());
    let mut rack = Rack::from_json(PITCH).unwrap();
    rack.reset(RATE).unwrap();
    assert!(rack.latency() > 0);
    assert_eq!(rack_latency(h), rack.latency() as i32);

    let input = sines(48000);
    let out = process(h, &input, None, 128);
    let mut frames: Vec<[f32; 2]> = input.chunks(2).map(|f| [f[0], f[1]]).collect();
    for block in frames.chunks_mut(128) {
        rack.process(block);
    }
    assert_eq!(out, frames.as_flattened());
    assert!(
        out.iter().any(|v| v.abs() > 0.1),
        "the pitch rack fell silent"
    );
    rack_free(h);
}

#[test]
fn processes_in_place_and_past_the_longest_side_block() {
    let input = sines(effect::MAX_BLOCK + 300);
    let a = new_rack(RATE, PITCH);
    let b = new_rack(RATE, PITCH);
    let apart = process(a, &input, None, input.len() / 2);
    let mut inplace = input.clone();
    let code = unsafe {
        rack_process(
            b,
            inplace.as_ptr(),
            ptr::null(),
            inplace.as_mut_ptr(),
            input.len() / 2,
        )
    };
    assert_eq!(code, 0, "{}", error());
    assert_eq!(apart, inplace);
    rack_free(a);
    rack_free(b);
}

#[test]
fn hands_the_side_signal_over() {
    let input = sines(24000);
    let loud: Vec<f32> = input.iter().map(|v| v * 1.5).collect();
    let silent = vec![0.0; input.len()];
    let runs: Vec<Vec<f32>> = [None, Some(&silent[..]), Some(&loud[..])]
        .into_iter()
        .map(|side| {
            let h = new_rack(RATE, DUCK);
            let out = process(h, &input, side, 128);
            rack_free(h);
            out
        })
        .collect();
    assert_eq!(runs[0], runs[1], "a silent side must sound as no side");
    let tail = |v: &[f32]| v[24000..].iter().map(|x| x * x).sum::<f32>();
    assert!(
        tail(&runs[2]) < 0.5 * tail(&runs[0]),
        "a loud side must duck the sound"
    );
}

#[test]
fn refuses_a_wrong_rack_with_a_message() {
    for (rate, json, says) in [
        (RATE, "{not json", "rack:"),
        (
            RATE,
            r#"{"effects": [{"effect": "nope"}]}"#,
            "no effect nope",
        ),
        (
            RATE,
            r#"{"effects": [{"effect": "gain", "params": {"db": 99}}]}"#,
            "outside",
        ),
        (1000.0, GAIN, "rate"),
        (f32::NAN, GAIN, "rate"),
    ] {
        assert_eq!(new_rack(rate, json), 0, "{json} at {rate}");
        assert!(error().contains(says), "{:?} lacks {says:?}", error());
    }
    assert_eq!(unsafe { rack_new(RATE, ptr::null(), 4) }, 0);
    assert!(!error().is_empty());
    let bad = [b'{', 0xff, b'}'];
    assert_eq!(unsafe { rack_new(RATE, bad.as_ptr(), bad.len()) }, 0);
    assert!(error().contains("UTF-8"), "{}", error());
}

#[test]
fn sets_params_and_refuses_wrong_ones() {
    let h = new_rack(RATE, GAIN);
    for (slot, name, value, says) in [
        (0, "x", 0.0, "gain has no param x"),
        (0, "db", 99.0, "outside"),
        (0, "db", f32::NAN, "outside"),
        (1, "db", 0.0, "no effect at 1"),
    ] {
        assert_eq!(set(h, slot, name, value), -1, "{slot} {name} {value}");
        assert!(error().contains(says), "{:?} lacks {says:?}", error());
    }
    let bad = [0xffu8];
    assert_eq!(unsafe { rack_set(h, 0, bad.as_ptr(), 1, 0.0) }, -1);
    assert_eq!(unsafe { rack_set(h, 0, ptr::null(), 2, 0.0) }, -1);

    assert_eq!(set(h, 0, "db", 0.0), 0, "{}", error());
    let input = sines(48000);
    let out = process(h, &input, None, 128);
    // the gain glides to its new value; a second on it has arrived
    for (x, y) in input[2 * 24000..].iter().zip(&out[2 * 24000..]) {
        assert!((x - y).abs() < 1e-4, "{y} != {x}");
    }
    rack_free(h);
}

#[test]
fn refuses_a_handle_it_did_not_give_or_has_freed() {
    let input = sines(128);
    let mut out = vec![7.0; input.len()];
    let run = |h: u32, out: &mut [f32]| unsafe {
        rack_process(h, input.as_ptr(), ptr::null(), out.as_mut_ptr(), 128)
    };
    for h in [0, 12345, u32::MAX] {
        assert_eq!(run(h, &mut out), -1);
        assert!(error().contains("no rack"), "{}", error());
        assert_eq!(rack_latency(h), -1);
        assert_eq!(set(h, 0, "db", 0.0), -1);
        rack_free(h);
    }
    let old = new_rack(RATE, GAIN);
    rack_free(old);
    rack_free(old);
    assert_eq!(run(old, &mut out), -1);
    assert!(
        out.iter().all(|&v| v == 7.0),
        "a refused call wrote the output"
    );
    // the freed slot is taken again, under a handle the old one does not match
    let new = new_rack(RATE, GAIN);
    assert_ne!(new, 0);
    assert_ne!(new, old);
    assert_eq!(run(old, &mut out), -1);
    assert_eq!(run(new, &mut out), 0);
    rack_free(new);
}

#[test]
fn checks_the_buffers_it_is_handed() {
    let h = new_rack(RATE, DUCK);
    let input = sines(256);
    // the output in the first half, room for a side after it
    let mut buf = vec![7.0f32; 2 * input.len()];
    let p = buf.as_mut_ptr();
    unsafe {
        assert_eq!(rack_process(h, ptr::null(), ptr::null(), p, 256), -1);
        assert_eq!(
            rack_process(h, input.as_ptr(), ptr::null(), ptr::null_mut(), 256),
            -1
        );
        // the side may not overlap the output, at its first or its last frame
        assert_eq!(rack_process(h, input.as_ptr(), p, p, 256), -1);
        assert_eq!(rack_process(h, input.as_ptr(), p.add(510), p, 256), -1);
        assert!(error().contains("overlap"), "{}", error());
        assert!(
            buf.iter().all(|&v| v == 7.0),
            "a refused call wrote the output"
        );
        assert_eq!(rack_process(h, input.as_ptr(), p.add(512), p, 256), 0);
        assert_eq!(
            rack_process(h, ptr::null(), ptr::null(), ptr::null_mut(), 0),
            0
        );
        assert_eq!(
            rack_process(h, input.as_ptr(), ptr::null(), p, usize::MAX / 4),
            -1
        );
    }
    rack_free(h);
}

#[test]
fn lists_the_effects_as_the_core_does() {
    let a = effects_json();
    let b = effects_json();
    assert_eq!(a, b, "the list is made once and kept");
    let text = unsafe { CStr::from_ptr(a) }.to_str().unwrap();
    assert_eq!(text, effect::effects_json());
    assert!(text.contains(r#""effect":"pitch""#));
}
