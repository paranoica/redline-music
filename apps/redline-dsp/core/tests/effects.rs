use redline_dsp_core::effect::{make, Effect, EFFECTS};
use std::f32::consts::PI;

const RATE: f32 = 48000.0;

fn effect(name: &str, params: &[(&str, f32)]) -> Box<dyn Effect> {
    let mut e = make(name).unwrap();
    for (p, v) in params {
        e.set(p, *v).unwrap();
    }
    e.reset(RATE);
    e
}

fn sine(freq: f32, amp: f32) -> Vec<[f32; 2]> {
    (0..RATE as usize)
        .map(|i| {
            let v = amp * (2.0 * PI * freq * i as f32 / RATE).sin();
            [v, v]
        })
        .collect()
}

fn noise(n: usize) -> Vec<[f32; 2]> {
    (0..n)
        .map(|i| {
            let r = |k: f64| (((i as f64 + k) * 12.9898).sin() * 43758.5453).fract() as f32 * 0.8;
            [r(0.0), r(0.5)]
        })
        .collect()
}

fn run(e: &mut dyn Effect, x: &[[f32; 2]], block: usize) -> Vec<[f32; 2]> {
    let mut y = x.to_vec();
    for chunk in y.chunks_mut(block) {
        e.process(chunk);
    }
    y
}

/// Peak of the last quarter of a channel: the steady state of a filtered sine.
fn tail(y: &[[f32; 2]], ch: usize) -> f32 {
    y[y.len() * 3 / 4..]
        .iter()
        .map(|f| f[ch].abs())
        .fold(0.0, f32::max)
}

fn db(v: f32) -> f32 {
    10f32.powf(v / 20.0)
}

#[test]
fn every_effect_has_its_specs_in_range_and_rejects_values_outside() {
    assert_eq!(
        EFFECTS,
        [
            "gain",
            "pan",
            "width",
            "eq",
            "filter",
            "saturate",
            "bitcrush",
            "pitch",
            "formant",
            "octaver",
            "harmonizer",
            "unison",
            "vibrato",
            "drift",
            "robot",
            "tune",
            "varispeed",
            "timestretch",
            "tape_stop",
            "tape_start",
            "reverse",
            "freeze",
            "granular",
            "stutter",
            "chop",
            "trance_gate",
            "room",
            "plate",
            "hall",
            "shimmer",
            "gated_reverb",
            "reverse_reverb",
            "delay",
            "tape_delay",
            "throws",
            "chorus",
            "flanger",
            "phaser",
            "doubler",
            "autopan"
        ]
    );
    for name in EFFECTS {
        let mut e = make(name).unwrap();
        assert_eq!(e.name(), *name);
        assert!(!e.params().is_empty());
        for p in e.params() {
            assert!(
                p.low <= p.default && p.default <= p.high,
                "{name}.{}",
                p.name
            );
            assert!(e.set(p.name, p.low).is_ok());
            assert!(e.set(p.name, p.high).is_ok());
            assert!(e.set(p.name, p.default).is_ok());
            let span = (p.high - p.low).max(1.0);
            for bad in [p.high + span * 0.01, p.low - span * 0.01, f32::NAN] {
                let err = e.set(p.name, bad).unwrap_err().to_string();
                assert!(err.contains(p.name) && err.contains(name), "{err}");
            }
        }
        let err = e.set("nonesuch", 0.0).unwrap_err().to_string();
        assert!(err.contains("nonesuch") && err.contains(name), "{err}");
    }
    assert!(make("nonesuch").is_none());
}

#[test]
fn neutral_settings_leave_the_sound_alone() {
    let x = noise(1000);
    for (name, params) in [
        ("gain", vec![]),
        ("pan", vec![]),
        ("width", vec![]),
        ("eq", vec![]),
        ("saturate", vec![("mix", 0.0)]),
    ] {
        let y = run(effect(name, &params).as_mut(), &x, 64);
        for (a, b) in x.iter().zip(&y) {
            for ch in 0..2 {
                assert!((a[ch] - b[ch]).abs() < 1e-6, "{name}");
            }
        }
    }
    // 24 bits: within half a step of 2^-23
    let y = run(effect("bitcrush", &[]).as_mut(), &x, 64);
    for (a, b) in x.iter().zip(&y) {
        assert!((a[0] - b[0]).abs() <= 0.5 / 8_388_608.0 + 1e-9);
    }
}

#[test]
fn gain_scales_by_its_decibels() {
    let x = noise(300);
    let y = run(effect("gain", &[("db", -6.0)]).as_mut(), &x, 7);
    for (a, b) in x.iter().zip(&y) {
        assert!((b[0] - a[0] * db(-6.0)).abs() < 1e-6);
        assert!((b[1] - a[1] * db(-6.0)).abs() < 1e-6);
    }
}

#[test]
fn pan_turns_down_the_far_side_only() {
    let x = noise(200);
    for (pan, left, right) in [
        (1.0, 0.0, 1.0),
        (-1.0, 1.0, 0.0),
        (0.5, (PI / 4.0).cos(), 1.0),
        (-0.5, 1.0, (PI / 4.0).cos()),
    ] {
        let y = run(effect("pan", &[("pan", pan)]).as_mut(), &x, 64);
        for (a, b) in x.iter().zip(&y) {
            assert!((b[0] - a[0] * left).abs() < 1e-6, "pan {pan}");
            assert!((b[1] - a[1] * right).abs() < 1e-6, "pan {pan}");
        }
    }
}

#[test]
fn width_scales_the_side_and_keeps_the_mid() {
    let x = vec![[0.5f32, 0.1]; 10];
    let y = run(effect("width", &[("width", 0.0)]).as_mut(), &x, 4);
    assert!(y
        .iter()
        .all(|f| (f[0] - 0.3).abs() < 1e-6 && (f[1] - 0.3).abs() < 1e-6));
    let y = run(effect("width", &[("width", 2.0)]).as_mut(), &x, 4);
    assert!(y
        .iter()
        .all(|f| (f[0] - 0.7).abs() < 1e-6 && (f[1] + 0.1).abs() < 1e-6));
}

#[test]
fn an_eq_peak_raises_its_frequency_by_its_gain() {
    let mut e = effect(
        "eq",
        &[("kind", 0.0), ("freq", 1000.0), ("gain", 6.0), ("q", 1.0)],
    );
    let y = run(e.as_mut(), &sine(1000.0, 0.25), 64);
    assert!((tail(&y, 0) / 0.25 - db(6.0)).abs() < 0.01 * db(6.0));
    assert!((tail(&y, 1) / 0.25 - db(6.0)).abs() < 0.01 * db(6.0));
}

#[test]
fn eq_shelves_move_one_end_and_leave_the_other() {
    // kinds: peak, lowshelf, highshelf, lowpass, highpass, bandpass
    let mut low = effect("eq", &[("kind", 1.0), ("freq", 1000.0), ("gain", 6.0)]);
    assert!((tail(&run(low.as_mut(), &sine(30.0, 0.25), 64), 0) / 0.25 - db(6.0)).abs() < 0.02);
    low.reset(RATE);
    assert!((tail(&run(low.as_mut(), &sine(15000.0, 0.25), 64), 0) / 0.25 - 1.0).abs() < 0.02);
    let mut high = effect("eq", &[("kind", 2.0), ("freq", 1000.0), ("gain", -6.0)]);
    assert!(
        (tail(&run(high.as_mut(), &sine(15000.0, 0.25), 64), 0) / 0.25 - db(-6.0)).abs() < 0.02
    );
    high.reset(RATE);
    assert!((tail(&run(high.as_mut(), &sine(30.0, 0.25), 64), 0) / 0.25 - 1.0).abs() < 0.02);
}

#[test]
fn a_param_set_after_reset_takes_effect() {
    let mut e = effect("eq", &[("freq", 1000.0), ("q", 1.0)]);
    e.set("gain", 6.0).unwrap();
    let y = run(e.as_mut(), &sine(1000.0, 0.25), 64);
    assert!((tail(&y, 0) / 0.25 - db(6.0)).abs() < 0.01 * db(6.0));
}

#[test]
fn the_filter_passes_its_band_and_cuts_the_rest() {
    // modes: lp, hp, bp
    for (mode, pass, stop) in [
        (0.0, vec![100.0], vec![10000.0]),
        (1.0, vec![10000.0], vec![100.0]),
        (2.0, vec![1000.0], vec![100.0, 10000.0]),
    ] {
        let mut e = effect("filter", &[("mode", mode), ("cutoff", 1000.0)]);
        for f in pass {
            e.reset(RATE);
            let g = tail(&run(e.as_mut(), &sine(f, 0.25), 64), 0) / 0.25;
            assert!((g - 1.0).abs() < 0.02, "mode {mode} at {f}: {g}");
        }
        for f in stop {
            e.reset(RATE);
            let g = tail(&run(e.as_mut(), &sine(f, 0.25), 64), 0) / 0.25;
            let most = if mode == 2.0 { 0.2 } else { 0.02 };
            assert!(g < most, "mode {mode} at {f}: {g}");
        }
    }
}

#[test]
fn resonance_peaks_a_low_pass_at_its_cutoff() {
    let mut e = effect("filter", &[("cutoff", 1000.0), ("resonance", 4.0)]);
    let g = tail(&run(e.as_mut(), &sine(1000.0, 0.1), 64), 0) / 0.1;
    assert!((g - 4.0).abs() < 0.08, "{g}");
}

#[test]
fn reset_forgets_the_past() {
    let mut used = effect("filter", &[("cutoff", 500.0), ("resonance", 8.0)]);
    run(used.as_mut(), &noise(500), 64);
    used.reset(RATE);
    let mut fresh = effect("filter", &[("cutoff", 500.0), ("resonance", 8.0)]);
    let x = noise(300);
    assert_eq!(run(used.as_mut(), &x, 64), run(fresh.as_mut(), &x, 64));
}

#[test]
fn saturation_is_a_driven_tanh_mixed_with_the_dry_sound() {
    let x = noise(500);
    let y = run(effect("saturate", &[("drive", 12.0)]).as_mut(), &x, 64);
    for (a, b) in x.iter().zip(&y) {
        assert!((b[0] - (db(12.0) * a[0]).tanh()).abs() < 1e-6);
    }
    let y = run(
        effect("saturate", &[("drive", 12.0), ("mix", 0.5)]).as_mut(),
        &x,
        64,
    );
    for (a, b) in x.iter().zip(&y) {
        let want = 0.5 * (db(12.0) * a[1]).tanh() + 0.5 * a[1];
        assert!((b[1] - want).abs() < 1e-6);
    }
}

#[test]
fn bitcrush_quantises_to_its_bits_and_holds_across_blocks() {
    let x = noise(400);
    let y = run(effect("bitcrush", &[("bits", 2.0)]).as_mut(), &x, 64);
    for (a, b) in x.iter().zip(&y) {
        // two bits: steps of a half
        assert!((b[0] * 2.0).fract() == 0.0, "{}", b[0]);
        assert!((b[0] - a[0]).abs() <= 0.25 + 1e-6);
    }
    // blocks of three frames against a hold of four: the hold carries over
    let y = run(effect("bitcrush", &[("hold", 4.0)]).as_mut(), &x, 3);
    for (k, group) in y.chunks(4).enumerate() {
        let first = x[k * 4];
        for f in group {
            assert!((f[0] - first[0]).abs() < 1e-6 && (f[1] - first[1]).abs() < 1e-6);
        }
    }
}

/// A sine with some side content.
fn stereo_sine(freq: f32, secs: f32) -> Vec<[f32; 2]> {
    (0..(secs * RATE) as usize)
        .map(|i| {
            let v = 0.5 * (2.0 * PI * freq * i as f32 / RATE).sin();
            [v, -0.5 * v]
        })
        .collect()
}

fn largest_step(y: &[[f32; 2]]) -> f32 {
    y.windows(2)
        .flat_map(|w| [(w[1][0] - w[0][0]).abs(), (w[1][1] - w[0][1]).abs()])
        .fold(0.0, f32::max)
}

/// The largest step after `param` jumps to `to` mid-sound, and the largest step of the same
/// sound with the effect settled at either value from the start.
fn step_after_set(
    name: &str,
    before: &[(&str, f32)],
    param: &str,
    to: f32,
    x: &[[f32; 2]],
    split: usize,
) -> (f32, f32) {
    let mut e = effect(name, before);
    let mut y = x.to_vec();
    for chunk in y[..split].chunks_mut(64) {
        e.process(chunk);
    }
    e.set(param, to).unwrap();
    for chunk in y[split..].chunks_mut(64) {
        e.process(chunk);
    }
    let mut settled: Vec<(&str, f32)> = before.to_vec();
    settled.push((param, to));
    let steady_after = run(effect(name, &settled).as_mut(), x, 64);
    let steady_before = run(effect(name, before).as_mut(), x, 64);
    let steady = largest_step(&steady_after[split..]).max(largest_step(&steady_before[split..]));
    (largest_step(&y[split - 1..]), steady)
}

type Case<'a> = (&'a str, &'a [(&'a str, f32)], &'a str, f32, &'a [[f32; 2]]);

#[test]
fn a_set_while_playing_does_not_click() {
    let low = stereo_sine(200.0, 0.5);
    let band = stereo_sine(1000.0, 0.5);
    // 50 periods of 200 Hz and a quarter more: both sines at their peak
    let split = 12060;
    let cases: &[Case] = &[
        ("gain", &[("db", -60.0)], "db", 0.0, &low),
        ("gain", &[("db", 0.0)], "db", -60.0, &low),
        ("pan", &[("pan", -1.0)], "pan", 1.0, &low),
        ("width", &[("width", 0.0)], "width", 2.0, &low),
        ("saturate", &[("drive", 0.0)], "drive", 12.0, &low),
        (
            "saturate",
            &[("drive", 12.0), ("mix", 0.0)],
            "mix",
            1.0,
            &low,
        ),
        (
            "eq",
            &[("freq", 1000.0), ("gain", -24.0)],
            "gain",
            24.0,
            &band,
        ),
        // a low shelf sliding down past the tone: the level falls 12 dB, never through a peak
        (
            "eq",
            &[("kind", 1.0), ("freq", 5000.0), ("gain", 12.0)],
            "freq",
            100.0,
            &band,
        ),
        (
            "eq",
            &[("freq", 1000.0), ("gain", 12.0)],
            "kind",
            3.0,
            &band,
        ),
        ("filter", &[("cutoff", 200.0)], "cutoff", 5000.0, &band),
        ("filter", &[("cutoff", 1000.0)], "mode", 1.0, &band),
        ("octaver", &[("down", 1.0), ("dry", 1.0)], "dry", 0.0, &low),
        ("octaver", &[("up", 0.0), ("dry", 1.0)], "up", 1.0, &low),
        ("harmonizer", &[("mix", 0.0)], "mix", 1.0, &low),
        ("unison", &[("mix", 0.0)], "mix", 1.0, &low),
        (
            "tune",
            &[("key", 9.0), ("scale", 5.0), ("mix", 0.0)],
            "mix",
            1.0,
            &low,
        ),
        ("robot", &[("mix", 0.0)], "mix", 1.0, &low),
    ];
    for (name, before, param, to, x) in cases {
        let (step, steady) = step_after_set(name, before, param, *to, x, split);
        let bound = 1.25 * steady + 2e-3;
        eprintln!("{name}.{param} -> {to}: step {step}, settled {steady}");
        assert!(
            step <= bound,
            "{name}.{param} -> {to}: a step of {step}, settled {steady}"
        );
    }
}

#[test]
fn a_filter_ringing_out_never_leaves_subnormal_samples() {
    // a 20 Hz pole pair at q 18 rings for minutes before its state underflows by itself
    let mut e = effect("eq", &[("kind", 3.0), ("freq", 20.0), ("q", 18.0)]);
    let mut x = vec![[0.0f32; 2]; 40 * RATE as usize];
    x[0] = [1.0, -1.0];
    let y = run(e.as_mut(), &x, 512);
    let tiny = y.iter().flatten().filter(|v| v.is_subnormal()).count();
    assert_eq!(tiny, 0, "{tiny} subnormal samples");
    assert!(y[y.len() - 48000..].iter().all(|f| *f == [0.0, 0.0]));
    assert!(y[..48000].iter().any(|f| f[0].abs() > 1e-4));
}

#[test]
fn every_effect_survives_a_rate_too_low_for_it() {
    let x = noise(2000);
    for name in EFFECTS {
        for rate in [10.0, 4000.0] {
            let mut e = make(name).unwrap();
            e.reset(rate);
            let y = run(e.as_mut(), &x, 256);
            assert!(
                y.iter().flatten().all(|v| v.is_finite()),
                "{name} at {rate}"
            );
        }
    }
}

#[cfg(target_arch = "x86_64")]
#[test]
fn flush_tiny_zeroes_subnormal_arithmetic_until_dropped() {
    use redline_dsp_core::filters::flush_tiny;
    let half = || std::hint::black_box(1e-39f32) * std::hint::black_box(0.5f32);
    assert!(half() != 0.0);
    {
        let _flushed = flush_tiny();
        assert_eq!(half(), 0.0);
    }
    assert!(half() != 0.0);
}
