//! The time effects on generated sound: what comes out is measured, not compared to a capture.

use redline_dsp_core::effect::{make, Effect};
use std::f64::consts::PI;

const RATE: f32 = 48000.0;

fn effect(name: &str, params: &[(&str, f32)]) -> Box<dyn Effect> {
    let mut e = make(name).unwrap_or_else(|| panic!("no effect {name}"));
    for (p, v) in params {
        e.set(p, *v).unwrap();
    }
    e.reset(RATE);
    e
}

fn run(e: &mut dyn Effect, x: &[[f32; 2]], block: usize) -> Vec<[f32; 2]> {
    let mut y = x.to_vec();
    for chunk in y.chunks_mut(block) {
        e.process(chunk);
    }
    y
}

fn frames(secs: f32) -> usize {
    (secs * RATE).round() as usize
}

/// A tone whose frequency follows `f(t)`, the phase integrated so changes stay smooth.
fn tone(secs: f32, f: impl Fn(f64) -> f64) -> Vec<[f32; 2]> {
    let mut phase = 0.0f64;
    (0..frames(secs))
        .map(|i| {
            let v = (0.5 * phase.sin()) as f32;
            phase += 2.0 * PI * f(i as f64 / RATE as f64) / RATE as f64;
            [v, v]
        })
        .collect()
}

fn sine(hz: f64, secs: f32) -> Vec<[f32; 2]> {
    tone(secs, |_| hz)
}

/// A rising line, a different one on each channel, so every frame says where it came from.
fn ramp(secs: f32) -> Vec<[f32; 2]> {
    let n = frames(secs) as f32;
    (0..frames(secs))
        .map(|i| [i as f32 / n, -(i as f32) / n])
        .collect()
}

fn noise(secs: f32) -> Vec<[f32; 2]> {
    (0..frames(secs))
        .map(|i| {
            let r = |k: f64| (((i as f64 + k) * 12.9898).sin() * 43758.5453).fract() as f32 * 0.8;
            [r(0.0), r(0.5)]
        })
        .collect()
}

fn channel(y: &[[f32; 2]], ch: usize) -> Vec<f32> {
    y.iter().map(|f| f[ch]).collect()
}

/// Frequency of a sine-like stretch from its upward zero crossings, interpolated.
fn crossing_freq(x: &[f32]) -> Option<f64> {
    let mut times = Vec::new();
    for i in 1..x.len() {
        let (a, b) = (x[i - 1] as f64, x[i] as f64);
        if a < 0.0 && b >= 0.0 {
            times.push(i as f64 - 1.0 + a / (a - b));
        }
    }
    if times.len() < 3 {
        return None;
    }
    let span = times[times.len() - 1] - times[0];
    Some((times.len() - 1) as f64 * RATE as f64 / span)
}

/// The crossing frequency of channel 0 between two times in seconds.
fn freq_between(y: &[[f32; 2]], from: f32, to: f32) -> f64 {
    crossing_freq(&channel(&y[frames(from)..frames(to)], 0))
        .unwrap_or_else(|| panic!("no tone between {from} and {to}"))
}

fn fft(re: &mut [f64], im: &mut [f64]) {
    let n = re.len();
    let mut j = 0;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j |= bit;
        if i < j {
            re.swap(i, j);
            im.swap(i, j);
        }
    }
    let mut len = 2;
    while len <= n {
        let w = -2.0 * PI / len as f64;
        for start in (0..n).step_by(len) {
            for k in 0..len / 2 {
                let (s, c) = (w * k as f64).sin_cos();
                let (a, b) = (start + k, start + k + len / 2);
                let tr = re[b] * c - im[b] * s;
                let ti = re[b] * s + im[b] * c;
                re[b] = re[a] - tr;
                im[b] = im[a] - ti;
                re[a] += tr;
                im[a] += ti;
            }
        }
        len <<= 1;
    }
}

/// The strongest frequency of a channel, Hann windowed and zero padded.
fn peak(x: &[f32]) -> f64 {
    let n = (x.len() * 4).next_power_of_two();
    let (mut re, mut im) = (vec![0.0; n], vec![0.0; n]);
    let m = x.len() as f64;
    for (i, v) in x.iter().enumerate() {
        re[i] = *v as f64 * (0.5 - 0.5 * (2.0 * PI * i as f64 / m).cos());
    }
    fft(&mut re, &mut im);
    let k = (1..n / 2)
        .max_by(|&a, &b| re[a].hypot(im[a]).total_cmp(&re[b].hypot(im[b])))
        .unwrap();
    k as f64 * RATE as f64 / n as f64
}

fn rms(x: &[[f32; 2]]) -> f64 {
    (x.iter()
        .map(|f| (f[0] as f64).powi(2) + (f[1] as f64).powi(2))
        .sum::<f64>()
        / (2 * x.len()) as f64)
        .sqrt()
}

fn assert_same(got: &[[f32; 2]], want: &[[f32; 2]], tolerance: f32, what: &str) {
    assert_eq!(got.len(), want.len(), "{what}: length");
    for (i, (a, b)) in got.iter().zip(want).enumerate() {
        for c in 0..2 {
            assert!(
                (a[c] - b[c]).abs() <= tolerance,
                "{what}: frame {i} channel {c}: {} against {}",
                a[c],
                b[c]
            );
        }
    }
}

// --- varispeed ---

#[test]
fn varispeed_at_speed_one_is_the_input() {
    let x = noise(1.0);
    let y = run(effect("varispeed", &[]).as_mut(), &x, 100);
    assert_same(&y, &x, 0.0, "speed 1");
}

#[test]
fn varispeed_moves_the_pitch_with_the_speed() {
    let x = sine(440.0, 3.0);
    for (speed, want) in [(2.0, 880.0), (0.5, 220.0), (1.25, 550.0)] {
        let y = run(
            effect("varispeed", &[("speed", speed), ("window", 1000.0)]).as_mut(),
            &x,
            256,
        );
        assert_eq!(y.len(), x.len());
        let got = peak(&channel(&y[frames(0.5)..], 0));
        assert!((got / want - 1.0).abs() < 0.01, "speed {speed}: {got}");
    }
    // between splices the tone is clean: splices are 10 ms a second at most, so nearly every
    // 50 ms stretch is an exact 880
    let y = run(
        effect("varispeed", &[("speed", 2.0), ("window", 1000.0)]).as_mut(),
        &x,
        64,
    );
    let stretches = channel(&y, 0);
    let stretches: Vec<f64> = stretches[frames(0.2)..]
        .chunks_exact(frames(0.05))
        .filter_map(crossing_freq)
        .collect();
    let clean = stretches
        .iter()
        .filter(|f| (*f - 880.0).abs() < 0.5)
        .count();
    assert!(
        clean as f64 > 0.85 * stretches.len() as f64,
        "{stretches:?}"
    );
    // with no window of past yet, the first loops are what has come in so far, not silence
    for quarter in y[..frames(1.0)].chunks(frames(0.25)) {
        assert!(rms(quarter) > 0.2, "{}", rms(quarter));
    }
}

#[test]
fn varispeed_reads_at_its_speed_never_ahead_of_the_input_and_loops_back_a_window() {
    let x = ramp(3.0);
    let step = 1.0 / frames(3.0) as f32;
    let window = 0.1;
    for speed in [2.0f32, 0.5] {
        let y = run(
            effect(
                "varispeed",
                &[("speed", speed), ("window", window * 1000.0)],
            )
            .as_mut(),
            &x,
            128,
        );
        let mut along = 0;
        let mut splices = 0;
        let mut inside = false;
        let settled = frames(0.5);
        for n in 1..y.len() {
            // never ahead of the newest input, never more than a window and a splice behind
            assert!(y[n][0] <= x[n][0] + 1e-6, "{speed}: frame {n} reads ahead");
            assert!(
                x[n][0] - y[n][0] <= (frames(window) + frames(0.02)) as f32 * step,
                "{speed}: frame {n} lags too far"
            );
            assert!(
                (y[n][0] + y[n][1]).abs() < 1e-6,
                "{speed}: channels apart at {n}"
            );
            let slope = y[n][0] - y[n - 1][0];
            if (slope - speed * step).abs() < 0.05 * step {
                along += 1;
                inside = false;
            } else if !inside {
                splices += (n >= settled) as i32;
                inside = true;
            }
        }
        assert!(along as f32 > 0.8 * y.len() as f32, "{speed}: {along}");
        // a jump each time the head has gained or lost a whole window on the input
        let span = (y.len() - settled) as f32;
        let expected = (span * (speed - 1.0).abs() / frames(window) as f32) as i32;
        assert!(
            (splices - expected).abs() <= 2,
            "{speed}: {splices} splices, {expected} expected"
        );
    }
}

// --- timestretch ---

#[test]
fn timestretch_keeps_the_frequency() {
    let x = sine(440.0, 3.0);
    for speed in [2.0, 0.5, 1.0] {
        let y = run(
            effect("timestretch", &[("speed", speed), ("window", 1000.0)]).as_mut(),
            &x,
            512,
        );
        assert_eq!(y.len(), x.len());
        let late = &y[frames(1.0)..];
        let got = peak(&channel(late, 0));
        assert!((got - 440.0).abs() < 4.0, "speed {speed}: {got}");
        assert!(rms(late) > 0.5 * rms(&x[frames(1.0)..]), "speed {speed}");
    }
}

#[test]
fn timestretch_at_half_speed_moves_through_the_input_at_half_speed() {
    // 440 Hz for a second, then 660: at half speed the change comes out a second later
    let x = tone(3.0, |t| if t < 1.0 { 440.0 } else { 660.0 });
    let mut e = effect("timestretch", &[("speed", 0.5), ("window", 4000.0)]);
    let late = e.latency() as f32 / RATE;
    assert!(late < 0.2, "{late}");
    let y = run(e.as_mut(), &x, 300);
    let early = freq_between(&y, 1.3 + late, 1.7 + late);
    assert!((early - 440.0).abs() < 4.0, "{early}");
    let after = freq_between(&y, 2.4 + late, 2.9);
    assert!((after - 660.0).abs() < 6.0, "{after}");
}

// --- tape stop and start ---

#[test]
fn tape_stop_slows_the_pitch_to_nothing_and_then_is_silent() {
    let x = sine(440.0, 2.0);
    let y = run(
        effect(
            "tape_stop",
            &[("time", 0.5), ("duration", 1.0), ("curve", 1.0)],
        )
        .as_mut(),
        &x,
        128,
    );
    assert_same(&y[..frames(0.5)], &x[..frames(0.5)], 0.0, "before the stop");
    // a linear curve: at a quarter of the way the speed is three quarters
    let mut last = f64::INFINITY;
    for (u, want) in [(0.25, 330.0), (0.5, 220.0), (0.75, 110.0)] {
        let t = 0.5 + u as f32;
        let got = freq_between(&y, t - 0.05, t + 0.05);
        assert!((got / want - 1.0).abs() < 0.05, "at {u}: {got}");
        assert!(got < last);
        last = got;
    }
    assert!(y[frames(1.5)..].iter().all(|f| *f == [0.0, 0.0]));
}

#[test]
fn tape_stop_curve_shapes_the_fall() {
    let x = sine(440.0, 2.0);
    let at_half = |curve: f32| {
        let y = run(
            effect(
                "tape_stop",
                &[("time", 0.5), ("duration", 1.0), ("curve", curve)],
            )
            .as_mut(),
            &x,
            128,
        );
        freq_between(&y, 0.95, 1.05)
    };
    // speed (1 - u)^curve at u = 0.5
    assert!((at_half(2.0) / 110.0 - 1.0).abs() < 0.05);
    assert!((at_half(0.5) / (440.0 * 0.5f64.sqrt()) - 1.0).abs() < 0.05);
}

#[test]
fn tape_start_spins_up_from_silence_and_ends_in_time_with_the_input() {
    // the spin-up lags 0.5 s, a quarter cycle off 440.5 Hz, and at 1.5 s the live tone peaks:
    // a hard cut would jump by half the amplitude
    let x = sine(440.5, 2.0);
    let y = run(
        effect(
            "tape_start",
            &[("time", 0.5), ("duration", 1.0), ("curve", 1.0)],
        )
        .as_mut(),
        &x,
        128,
    );
    assert!(y[..frames(0.5)].iter().all(|f| *f == [0.0, 0.0]));
    let mut last = 0.0;
    for (u, want) in [(0.25, 110.125), (0.5, 220.25), (0.75, 330.375)] {
        let t = 0.5 + u as f32;
        let got = freq_between(&y, t - 0.05, t + 0.05);
        assert!((got / want - 1.0).abs() < 0.05, "at {u}: {got}");
        assert!(got > last);
        last = got;
    }
    // once up to speed it splices back onto the input within a few milliseconds, without a
    // jump: the sine at 0.5 moves under 0.03 a frame
    assert_same(
        &y[frames(1.53)..],
        &x[frames(1.53)..],
        1e-6,
        "after the start",
    );
    let around = &y[frames(1.45)..frames(1.55)];
    let jump = around
        .windows(2)
        .map(|w| (w[1][0] - w[0][0]).abs())
        .fold(0.0, f32::max);
    assert!(jump < 0.05, "{jump}");
}

// --- reverse ---

#[test]
fn reverse_plays_each_window_backwards_one_window_late() {
    let x = ramp(1.0);
    let mut e = effect("reverse", &[("window", 100.0), ("fade", 0.0)]);
    assert!(e.offline_only());
    let w = frames(0.1);
    assert_eq!(e.latency(), w);
    let y = run(e.as_mut(), &x, 333);
    assert!(y[..w].iter().all(|f| *f == [0.0, 0.0]));
    for k in 1..x.len() / w {
        for i in 0..w {
            assert_eq!(y[k * w + i], x[k * w - 1 - i], "window {k}, frame {i}");
        }
    }
}

#[test]
fn reverse_fades_only_the_window_edges() {
    let x = ramp(1.0);
    let y = run(
        effect("reverse", &[("window", 100.0), ("fade", 5.0)]).as_mut(),
        &x,
        64,
    );
    let (w, f) = (frames(0.1), frames(0.005));
    for k in 1..x.len() / w {
        for i in 0..w {
            let want = x[k * w - 1 - i];
            let got = y[k * w + i];
            if i >= f && i < w - f {
                assert_eq!(got, want, "window {k}, frame {i}");
            } else {
                assert!(got[0].abs() < want[0].abs() || want[0] == 0.0);
            }
        }
        assert!(y[k * w][0].abs() < 0.01 && y[k * w + w - 1][0].abs() < 0.01);
    }
}

// --- freeze ---

#[test]
fn freeze_loops_the_last_grain_while_held_and_lets_go_when_released() {
    let x = noise(2.0);
    let mut e = effect("freeze", &[("length", 100.0)]);
    let (t0, t1) = (frames(0.5), frames(1.5));
    let mut y = x.clone();
    e.process(&mut y[..t0]);
    e.set("hold", 1.0).unwrap();
    e.process(&mut y[t0..t1]);
    e.set("hold", 0.0).unwrap();
    e.process(&mut y[t1..]);
    assert_same(&y[..t0], &x[..t0], 0.0, "before the hold");
    let (l, settle) = (frames(0.1), frames(0.01));
    // periodic with the grain's length once faded in
    for n in t0 + settle..t1 - l {
        assert_eq!(y[n], y[n + l], "frame {n}");
    }
    // the loop starts at the beginning of the last length of input before the hold
    for p in settle..l / 2 {
        assert_eq!(y[t0 + p], x[t0 - l + p], "frame {p} of the loop");
    }
    assert!(rms(&y[t0 + settle..t1]) > 0.3 * rms(&x));
    assert_same(
        &y[t1 + settle..],
        &x[t1 + settle..],
        0.0,
        "after the release",
    );
}

// --- granular ---

#[test]
fn granular_is_deterministic_by_seed_and_bounded() {
    let x = noise(2.0);
    let params = [
        ("size", 60.0),
        ("density", 40.0),
        ("scatter", 200.0),
        ("jitter", 5.0),
        ("stretch", 0.7),
        ("seed", 7.0),
    ];
    let a = run(effect("granular", &params).as_mut(), &x, 256);
    let b = run(effect("granular", &params).as_mut(), &x, 1);
    assert_same(&a, &b, 0.0, "same seed, other blocks");
    let mut other = params;
    other[5].1 = 8.0;
    let c = run(effect("granular", &other).as_mut(), &x, 256);
    assert!(a.iter().zip(&c).any(|(p, q)| p != q));
    let (got, dry) = (rms(&a[frames(0.2)..]), rms(&x));
    assert!(got > 0.1 * dry && got < 2.0 * dry, "{got} against {dry}");
    let peak = a.iter().flatten().fold(0.0f32, |m, v| m.max(v.abs()));
    assert!(peak <= 2.0, "{peak}");
}

#[test]
fn granular_with_overlapping_still_grains_rebuilds_the_input() {
    // Hann grains half a grain apart sum to one: no jitter and stretch 1 give back the input
    let x = noise(1.0);
    let y = run(
        effect(
            "granular",
            &[("size", 80.0), ("density", 25.0), ("stretch", 1.0)],
        )
        .as_mut(),
        &x,
        100,
    );
    let from = frames(0.08);
    assert_same(&y[from..], &x[from..], 1e-5, "still grains");
    // ten at a time: 200 ms grains every 10 ms sum to ten, and are scaled back to one
    let y = run(
        effect(
            "granular",
            &[("size", 200.0), ("density", 100.0), ("stretch", 1.0)],
        )
        .as_mut(),
        &x,
        100,
    );
    let from = frames(0.2);
    assert_same(&y[from..], &x[from..], 1e-5, "dense still grains");
}

#[test]
fn granular_at_double_stretch_never_goes_silent() {
    let x = sine(440.0, 3.0);
    for stretch in [0.0, 2.0] {
        let y = run(
            effect("granular", &[("stretch", stretch), ("size", 50.0)]).as_mut(),
            &x,
            512,
        );
        for second in y[frames(0.1)..].chunks(frames(0.25)) {
            assert!(rms(second) > 0.05, "stretch {stretch}");
        }
    }
}

// --- stutter ---

#[test]
fn stutter_repeats_the_last_slice_exactly_n_times_then_returns_to_the_input() {
    let x = noise(1.5);
    let mut e = effect("stutter", &[("length", 125.0), ("repeats", 3.0)]);
    let t0 = frames(0.5);
    let (l, f) = (frames(0.125), frames(0.002));
    let mut y = x.clone();
    e.process(&mut y[..t0]);
    e.set("trigger", 1.0).unwrap();
    e.process(&mut y[t0..]);
    assert_same(&y[..t0], &x[..t0], 0.0, "before the trigger");
    for k in 0..3 {
        for i in f..l - f {
            assert_eq!(y[t0 + k * l + i], x[t0 - l + i], "repeat {k}, frame {i}");
        }
    }
    let end = t0 + 3 * l;
    // a fourth repeat would match the slice again; the input comes back instead
    assert!((f..l - f).any(|i| y[end + i] != x[t0 - l + i]));
    assert_same(&y[end + f..], &x[end + f..], 0.0, "after the repeats");
}

#[test]
fn stutter_falls_in_level_and_steps_in_pitch_per_repeat() {
    let x = noise(1.5);
    let t0 = frames(0.5);
    let (l, f) = (frames(0.1), frames(0.002));
    let triggered = |params: &[(&str, f32)]| {
        let mut e = effect("stutter", params);
        let mut y = x.clone();
        e.process(&mut y[..t0]);
        e.set("trigger", 1.0).unwrap();
        e.process(&mut y[t0..]);
        y
    };
    let y = triggered(&[("length", 100.0), ("repeats", 3.0), ("fall", 6.0)]);
    for k in 0..3 {
        let g = 10f32.powf(-6.0 * k as f32 / 20.0);
        for i in f..l - f {
            for c in 0..2 {
                let want = x[t0 - l + i][c] * g;
                assert!((y[t0 + k * l + i][c] - want).abs() < 1e-6, "repeat {k}");
            }
        }
    }
    // an octave up per repeat: the second plays the slice at double speed, then silence
    let y = triggered(&[("length", 100.0), ("repeats", 2.0), ("step", 12.0)]);
    for i in f..l / 2 - f {
        assert!(
            (y[t0 + l + i][0] - x[t0 - l + 2 * i][0]).abs() < 1e-6,
            "frame {i}"
        );
    }
    assert!(y[t0 + l + l / 2 + f..t0 + 2 * l]
        .iter()
        .all(|v| *v == [0.0, 0.0]));
}

#[test]
fn stutter_fires_on_the_edge_not_on_a_held_trigger() {
    let x = noise(0.5);
    let mut e = make("stutter").unwrap();
    e.set("trigger", 1.0).unwrap();
    e.reset(RATE);
    let y = run(e.as_mut(), &x, 64);
    assert_same(&y, &x, 0.0, "held from the start");
}

// --- chop ---

fn chop(params: &[(&str, f32)], pattern: &[f32]) -> Box<dyn Effect> {
    let mut e = make("chop").unwrap();
    for (p, v) in params {
        e.set(p, *v).unwrap();
    }
    e.set("steps", pattern.len() as f32).unwrap();
    for (j, v) in pattern.iter().enumerate() {
        e.set(&format!("s{j}"), *v).unwrap();
    }
    e.reset(RATE);
    e
}

#[test]
fn chop_in_order_is_the_input() {
    let x = noise(3.0);
    for bpm in [60.0, 97.0, 200.0] {
        let y = run(
            chop(&[("bpm", bpm)], &[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]).as_mut(),
            &x,
            100,
        );
        assert_same(&y, &x, 0.0, "in order");
    }
    // the defaults are in order too
    let y = run(effect("chop", &[]).as_mut(), &x, 512);
    assert_same(&y, &x, 0.0, "defaults");
}

#[test]
fn chop_repeats_slice_zero_for_a_pattern_of_zeros() {
    let x = noise(3.0);
    // 120 bpm: a sixteenth is 125 ms, 6000 frames
    let y = run(
        chop(&[("bpm", 120.0)], &[0.0, 0.0, 0.0, 0.0]).as_mut(),
        &x,
        256,
    );
    let (s, f) = (6000, frames(0.003));
    for q in 0..x.len() / s {
        let first = q / 4 * 4 * s;
        for i in f..s - f {
            assert_eq!(y[q * s + i], x[first + i], "sixteenth {q}, frame {i}");
        }
        // short fades where a slice is cut
        if q > 0 {
            assert!(y[q * s][0].abs() <= x[first][0].abs() / f as f32 + 1e-6);
        }
    }
}

#[test]
fn chop_takes_a_later_slice_from_the_cycle_before() {
    let x = noise(2.0);
    let y = run(chop(&[("bpm", 120.0)], &[1.0, 0.0]).as_mut(), &x, 256);
    let (s, f) = (6000, frames(0.003));
    // the first sixteenth has no cycle before it
    assert!(y[..s].iter().all(|v| *v == [0.0, 0.0]));
    for c in 1..x.len() / (2 * s) {
        for i in f..s - f {
            assert_eq!(y[2 * c * s + i], x[2 * (c - 1) * s + s + i], "cycle {c}");
            assert_eq!(y[2 * c * s + s + i], x[2 * c * s + i], "cycle {c}");
        }
    }
}

// --- trance gate ---

#[test]
fn trance_gate_turns_the_off_steps_down_by_its_depth() {
    let x = noise(2.0);
    let y = run(
        effect(
            "trance_gate",
            &[
                ("bpm", 120.0),
                ("depth", 20.0),
                ("attack", 1.0),
                ("release", 2.0),
            ],
        )
        .as_mut(),
        &x,
        128,
    );
    // the default pattern: on, off, on, off...
    let s = 6000;
    let settle = frames(0.002) + 1;
    for q in 0..x.len() / s {
        let gain = if q % 2 == 0 { 1.0 } else { 0.1 };
        for i in settle..s {
            for c in 0..2 {
                let want = x[q * s + i][c] * gain;
                assert!((y[q * s + i][c] - want).abs() < 1e-6, "step {q}, frame {i}");
            }
        }
    }
    let mut e = effect("trance_gate", &[("bpm", 120.0), ("s1", 1.0), ("s2", 0.0)]);
    let y = run(e.as_mut(), &x, 128);
    assert!(y[s + settle..2 * s]
        .iter()
        .zip(&x[s + settle..])
        .all(|(a, b)| a == b));
    assert!(y[2 * s + frames(0.02)..3 * s]
        .iter()
        .all(|f| f[0].abs() < 1e-5 && f[1].abs() < 1e-5));
}
