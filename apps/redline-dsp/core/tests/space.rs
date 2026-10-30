//! The space effects on generated sound: what comes out is measured, not compared to a capture.

use redline_dsp_core::effect::{make, Effect};
use std::f64::consts::PI;

const RATE: f32 = 48000.0;

fn effect(name: &str, params: &[(&str, f32)]) -> Box<dyn Effect> {
    let mut e = make(name).unwrap_or_else(|| panic!("no effect {name}"));
    for (p, v) in params {
        e.set(p, *v).unwrap_or_else(|err| panic!("{err}"));
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

fn frames(secs: f64) -> usize {
    (secs * RATE as f64).round() as usize
}

fn silence(secs: f64) -> Vec<[f32; 2]> {
    vec![[0.0; 2]; frames(secs)]
}

/// A single full-scale frame at the start, then silence.
fn click(secs: f64) -> Vec<[f32; 2]> {
    let mut x = silence(secs);
    x[0] = [1.0, 1.0];
    x
}

fn noise(n: usize) -> Vec<[f32; 2]> {
    (0..n)
        .map(|i| {
            let r = |k: f64| {
                ((((i as f64 + k) * 12.9898).sin() * 43758.5453).fract() as f32 - 0.5) * 0.8
            };
            [r(0.0), r(0.5)]
        })
        .collect()
}

fn sine(hz: f64, secs: f64) -> Vec<[f32; 2]> {
    (0..frames(secs))
        .map(|i| {
            let v = (0.5 * (2.0 * PI * hz * i as f64 / RATE as f64).sin()) as f32;
            [v, v]
        })
        .collect()
}

fn energy(x: &[[f32; 2]], ch: usize) -> f64 {
    x.iter().map(|f| (f[ch] as f64).powi(2)).sum()
}

fn sum(x: &[[f32; 2]], ch: usize) -> f64 {
    x.iter().map(|f| f[ch] as f64).sum()
}

fn peak(x: &[[f32; 2]]) -> f32 {
    x.iter().flatten().fold(0.0, |m, v| m.max(v.abs()))
}

// --- delay ---

#[test]
fn delay_echoes_at_its_time_and_each_repeat_is_feedback_quieter() {
    let d = frames(0.1);
    let y = run(
        effect(
            "delay",
            &[
                ("time", 100.0),
                ("feedback", 0.5),
                ("tone", 20000.0),
                ("mix", 1.0),
            ],
        )
        .as_mut(),
        &click(0.6),
        64,
    );
    assert!(peak(&y[..d]) < 1e-6, "nothing before the first echo");
    // the loop's low pass has a gain of 1 at 0 Hz: an echo's samples sum to its level
    for k in 1..=4 {
        let echo = &y[k * d..k * d + 200];
        let want = 0.5f64.powi(k as i32 - 1);
        for ch in 0..2 {
            let got = sum(echo, ch);
            assert!(
                (got / want - 1.0).abs() < 0.01,
                "echo {k}: {got} against {want}"
            );
        }
        assert!(peak(&y[k * d + 300..(k + 1) * d]) < 1e-4, "after echo {k}");
    }
}

#[test]
fn ping_pong_starts_left_and_alternates_sides() {
    let d = frames(0.1);
    let y = run(
        effect(
            "delay",
            &[
                ("mode", 1.0),
                ("time", 100.0),
                ("feedback", 0.5),
                ("tone", 20000.0),
                ("mix", 1.0),
            ],
        )
        .as_mut(),
        &click(0.6),
        64,
    );
    for k in 1..=4 {
        let echo = &y[k * d..k * d + 200];
        let (near, far) = if k % 2 == 1 { (0, 1) } else { (1, 0) };
        let want = 0.5f64.powi(k as i32 - 1);
        let got = sum(echo, near);
        assert!(
            (got / want - 1.0).abs() < 0.01,
            "echo {k}: {got} against {want}"
        );
        assert!(sum(echo, far).abs() < 1e-6, "echo {k} on the far side");
    }
}

#[test]
fn slapback_repeats_once() {
    let d = frames(0.1);
    let y = run(
        effect(
            "delay",
            &[
                ("mode", 2.0),
                ("time", 100.0),
                ("feedback", 0.9),
                ("tone", 20000.0),
                ("mix", 1.0),
            ],
        )
        .as_mut(),
        &click(0.6),
        64,
    );
    assert!((sum(&y[d..d + 200], 0) - 1.0).abs() < 0.01);
    assert!(peak(&y[d + 300..]) < 1e-6);
}

#[test]
fn a_synced_delay_lasts_its_note_at_the_tempo() {
    // 60 / bpm seconds a beat, times the note's beats
    for (note, bpm, beats) in [
        ("1/4", 90.0, 1.0),
        ("1/8", 120.0, 0.5),
        ("1/8 dotted", 100.0, 0.75),
        ("1/16", 150.0, 0.25),
        ("1/8 triplet", 90.0, 1.0 / 3.0),
    ] {
        let rack = format!(
            r#"{{"effects": [{{"effect": "delay", "params": {{"sync": "{note}", "bpm": {bpm}, "time": 1000, "mix": 1, "feedback": 0, "tone": 20000}}}}]}}"#
        );
        let mut rack = redline_dsp_core::effect::Rack::from_json(&rack).unwrap();
        rack.reset(RATE).unwrap();
        let mut y = click(1.5);
        for chunk in y.chunks_mut(128) {
            rack.process(chunk);
        }
        // the echo's centre, as the delay may fall between frames
        let want = 60.0 / bpm * beats * RATE as f64;
        let weight: f64 = y.iter().map(|f| f[0].abs() as f64).sum();
        let centre = y
            .iter()
            .enumerate()
            .map(|(i, f)| i as f64 * f[0].abs() as f64)
            .sum::<f64>()
            / weight;
        assert!(
            (centre - want).abs() < 0.1,
            "{note} at {bpm}: {centre} against {want}"
        );
    }
}

// --- tape delay ---

#[test]
fn tape_delay_echoes_near_its_time_wanders_by_seed_and_stays_bounded() {
    let x = click(1.0);
    let render = |seed: f32| {
        run(
            effect(
                "tape_delay",
                &[("time", 300.0), ("mix", 1.0), ("seed", seed), ("wow", 1.0)],
            )
            .as_mut(),
            &x,
            100,
        )
    };
    let a = render(1.0);
    assert_eq!(a, render(1.0));
    assert_ne!(a, render(2.0));
    // wow adds up to 6 ms and flutter up to 0.12 ms, never less than the time
    let first = a.iter().position(|f| f[0].abs() > 1e-3).unwrap();
    assert!(first >= frames(0.3) && first <= frames(0.3065), "{first}");
    let loud = run(
        effect(
            "tape_delay",
            &[("feedback", 0.95), ("drive", 24.0), ("mix", 1.0)],
        )
        .as_mut(),
        &noise(frames(4.0)),
        512,
    );
    assert!(loud
        .iter()
        .flatten()
        .all(|v| v.is_finite() && v.abs() < 2.0));
}

// --- throws ---

#[test]
fn throws_echo_the_end_of_each_phrase_only() {
    // phrases 0.2-1.3 s (with a 0.1 s breath at 0.7) and 2.3-2.6 s
    let mut x = silence(4.0);
    let burst = noise(frames(4.0));
    for (a, b) in [(0.2, 0.7), (0.8, 1.3), (2.3, 2.6)] {
        x[frames(a)..frames(b)].copy_from_slice(&burst[frames(a)..frames(b)]);
    }
    let mut e = effect(
        "throws",
        &[
            ("time", 400.0),
            ("tail", 300.0),
            ("gap", 200.0),
            ("feedback", 0.5),
            ("tone", 20000.0),
        ],
    );
    let lag = e.latency();
    assert_eq!(lag, frames(0.5));
    let mut padded = x.clone();
    padded.extend(silence(0.5));
    let y = run(e.as_mut(), &padded, 128);
    let y = &y[lag..];
    let wet: Vec<[f32; 2]> = y
        .iter()
        .zip(&x)
        .map(|(a, b)| [a[0] - b[0], a[1] - b[1]])
        .collect();
    // the dry sound comes through untouched, only late
    assert!(
        wet[..frames(1.4)].iter().all(|f| *f == [0.0, 0.0]),
        "no echo of the breath"
    );
    // the first phrase ends at 1.3 s: its last 0.3 s echoes 0.4 s later on the left, then on
    // the right 0.4 s after that
    let at = |a: f64, b: f64, ch: usize| energy(&wet[frames(a)..frames(b)], ch);
    assert!(at(1.45, 1.7, 0) > 100.0 * at(1.45, 1.7, 1));
    assert!(at(1.85, 2.1, 1) > 100.0 * at(1.85, 2.1, 0));
    assert!(at(1.45, 1.7, 0) > 1.0);
    // the whole tail, from about 1.0 s to the end near 1.33 s, and nothing after it
    assert!(at(1.45, 1.55, 0) > 1.0);
    assert!(at(1.76, 1.84, 0) < 1e-3 * at(1.45, 1.7, 0));
    // the second phrase ends at 2.6 s
    assert!(at(2.75, 3.0, 0) > 1.0);
}

// --- reverbs ---

/// RT60 from the backwards-integrated energy of an impulse response: the -5 to -25 dB slope.
fn rt60(ir: &[[f32; 2]]) -> f64 {
    let e: Vec<f64> = ir
        .iter()
        .map(|f| (f[0] as f64).powi(2) + (f[1] as f64).powi(2))
        .collect();
    let mut tail = vec![0.0; e.len() + 1];
    for i in (0..e.len()).rev() {
        tail[i] = tail[i + 1] + e[i];
    }
    let db = |i: usize| 10.0 * (tail[i] / tail[0]).log10();
    let cross = |level: f64| (0..e.len()).find(|&i| db(i) < level).unwrap();
    let (a, b) = (cross(-5.0), cross(-25.0));
    3.0 * (b - a) as f64 / RATE as f64
}

#[test]
fn reverb_responses_decay_and_ring_longer_with_more_decay() {
    for (name, short, long) in [("room", 0.5, 2.0), ("plate", 0.5, 3.0), ("hall", 1.0, 4.0)] {
        let ir = |decay: f32| {
            run(
                effect(name, &[("mix", 1.0), ("predelay", 0.0), ("decay", decay)]).as_mut(),
                &click(8.0),
                256,
            )
        };
        let (a, b) = (ir(short), ir(long));
        for y in [&a, &b] {
            assert!(y.iter().flatten().all(|v| v.is_finite()), "{name}");
            let first = energy(&y[..frames(0.5)], 0);
            let last = energy(&y[frames(7.5)..], 0);
            assert!(
                first > 0.0 && last < first * 1e-4,
                "{name}: {first} then {last}"
            );
        }
        let (ta, tb) = (rt60(&a), rt60(&b));
        assert!(
            tb > 1.5 * ta,
            "{name}: rt60 {ta} at {short} s, {tb} at {long} s"
        );
    }
}

#[test]
fn a_reverb_is_deterministic_and_waits_its_predelay() {
    for name in ["room", "plate", "hall", "shimmer"] {
        let render = || {
            run(
                effect(name, &[("mix", 1.0), ("predelay", 50.0)]).as_mut(),
                &click(1.0),
                100,
            )
        };
        let y = render();
        assert_eq!(y, render(), "{name}");
        assert!(
            peak(&y[..frames(0.05)]) == 0.0,
            "{name} before its predelay"
        );
        assert!(peak(&y[frames(0.05)..]) > 1e-3, "{name}");
    }
}

#[test]
fn the_reverb_mix_keeps_the_dry_sound_at_zero() {
    let x = noise(5000);
    for name in ["room", "plate", "hall", "shimmer", "gated_reverb"] {
        let y = run(effect(name, &[("mix", 0.0)]).as_mut(), &x, 64);
        assert_eq!(y, x, "{name}");
    }
}

/// Energy within 100 Hz of `hz`, from a plain DFT on channel 0.
fn band(x: &[[f32; 2]], hz: f64) -> f64 {
    let n = x.len() as f64;
    let mut total = 0.0;
    let step = RATE as f64 / n;
    let mut f = hz - 100.0;
    while f <= hz + 100.0 {
        let (mut re, mut im) = (0.0, 0.0);
        for (i, v) in x.iter().enumerate() {
            let w = 2.0 * PI * f * i as f64 / RATE as f64;
            re += v[0] as f64 * w.cos();
            im += v[0] as f64 * w.sin();
        }
        total += re * re + im * im;
        f += step;
    }
    total
}

#[test]
fn shimmer_feeds_an_octave_up_into_its_tail() {
    let mut x = sine(440.0, 0.5);
    x.extend(silence(2.5));
    let tail = |feedback: f32| {
        let y = run(
            effect(
                "shimmer",
                &[("mix", 1.0), ("feedback", feedback), ("decay", 4.0)],
            )
            .as_mut(),
            &x,
            256,
        );
        let t = &y[frames(1.5)..frames(1.75)];
        band(t, 880.0) / band(t, 440.0)
    };
    let (without, with) = (tail(0.0), tail(0.8));
    assert!(
        with > 10.0 * without,
        "880/440 energy: {without} without, {with} with"
    );
}

#[test]
fn a_gated_reverb_is_silent_after_hold_and_release() {
    let mut x = noise(frames(0.3));
    x.extend(silence(1.7));
    let y = run(
        effect(
            "gated_reverb",
            &[
                ("mix", 1.0),
                ("decay", 4.0),
                ("hold", 100.0),
                ("release", 50.0),
                ("threshold", -30.0),
            ],
        )
        .as_mut(),
        &x,
        128,
    );
    // the long room still rings while the gate holds
    assert!(energy(&y[frames(0.32)..frames(0.4)], 0) > 1.0);
    assert!(y[frames(0.3 + 0.1 + 0.05 + 0.001)..]
        .iter()
        .all(|f| *f == [0.0, 0.0]));
    // the same room ungated rings on
    let open = run(
        effect("room", &[("mix", 1.0), ("decay", 4.0)]).as_mut(),
        &x,
        128,
    );
    assert!(energy(&open[frames(0.5)..frames(1.0)], 0) > 1.0);
}

#[test]
fn a_reverse_reverb_swells_into_the_onset_and_is_offline_only() {
    let reverse = || effect("reverse_reverb", &[("mix", 1.0), ("decay", 1.5)]);
    let mut e = reverse();
    assert!(e.offline_only());
    let lag = e.latency();
    assert!(lag > 0);
    let onset = frames(3.0);
    let mut x = silence(3.0 + 1.0 + lag as f64 / RATE as f64);
    x[onset] = [1.0, 1.0];
    let y = run(e.as_mut(), &x, 512);
    let wet = &y[lag..];
    let window = |from: f64| {
        let a = onset as isize + frames(from.abs()) as isize * from.signum() as isize;
        energy(&wet[a as usize..a as usize + frames(0.1)], 0)
    };
    // 100 ms windows from a second before the onset: louder each one
    let before: Vec<f64> = (0..10).map(|k| window(-1.0 + 0.1 * k as f64)).collect();
    for w in before.windows(2) {
        assert!(w[1] > w[0], "{before:?}");
    }
    assert!(before[0] > 0.0);
    // nothing after the onset but the click itself through the reverb's first frames
    assert!(wet[onset + frames(0.01)..]
        .iter()
        .all(|f| f[0].abs() < 1e-6));
    // a pre-delay ends the swell that long before the onset
    let mut e = effect(
        "reverse_reverb",
        &[("mix", 1.0), ("decay", 1.5), ("predelay", 200.0)],
    );
    let y = run(e.as_mut(), &x, 512);
    let wet = &y[lag..];
    assert!(wet[onset - frames(0.19)..]
        .iter()
        .all(|f| f[0].abs() < 1e-6));
    assert!(energy(&wet[onset - frames(0.3)..onset - frames(0.2)], 0) > 0.0);
}

// --- modulation ---

/// An impulse every 10 ms, so windows of two periods all hold the same input.
fn pulses(secs: f64) -> Vec<[f32; 2]> {
    (0..frames(secs))
        .map(|i| if i % 480 == 0 { [0.5, 0.5] } else { [0.0, 0.0] })
        .collect()
}

/// Channel 0's harmonics of 100 Hz from 200 Hz to 4 kHz, in windows of 20 ms every 10 ms.
fn spectra(y: &[[f32; 2]]) -> Vec<Vec<f64>> {
    let (n, hop) = (960, 480);
    (0..(y.len() - n) / hop)
        .map(|w| {
            let x = &y[w * hop..w * hop + n];
            (2..=40)
                .map(|h| {
                    let (mut re, mut im) = (0.0, 0.0);
                    for (i, v) in x.iter().enumerate() {
                        // harmonic h sits in bin 2h of a 960-frame window
                        let a = 2.0 * PI * (2 * h * i) as f64 / n as f64;
                        re += v[0] as f64 * a.cos();
                        im += v[0] as f64 * a.sin();
                    }
                    re.hypot(im)
                })
                .collect()
        })
        .collect()
}

/// How far the spectrum moves over `lag` windows, on average.
fn distance(s: &[Vec<f64>], lag: usize) -> f64 {
    let n = s.len() - lag;
    (0..n)
        .map(|i| {
            s[i].iter()
                .zip(&s[i + lag])
                .map(|(a, b)| (a - b).powi(2))
                .sum::<f64>()
        })
        .sum::<f64>()
        / n as f64
}

#[test]
fn chorus_flanger_and_phaser_move_the_spectrum_at_their_rate() {
    for (name, rate, params) in [
        ("chorus", 2.0, vec![("voices", 1.0)]),
        ("flanger", 1.0, vec![]),
        ("phaser", 2.0, vec![]),
        ("phaser", 0.5, vec![("stages", 8.0)]),
    ] {
        let mut p = params.clone();
        p.push(("rate", rate));
        let y = run(effect(name, &p).as_mut(), &pulses(5.0), 256);
        // the first half second settles the delays and the feedback
        let s = spectra(&y[frames(0.5)..]);
        // 100 windows a second: the period in windows
        let period = (100.0 / rate as f64).round() as usize;
        let lags = period / 2..=period * 3 / 2;
        let best = lags
            .clone()
            .min_by(|&a, &b| distance(&s, a).total_cmp(&distance(&s, b)))
            .unwrap();
        assert!(
            best.abs_diff(period) <= 1,
            "{name} at {rate} Hz repeats every {best} windows"
        );
        // it does move: half a period on, the spectrum is far from where it was
        let (again, half) = (distance(&s, period), distance(&s, period / 2));
        assert!(
            again < 0.01 * half,
            "{name}: {again} a period on, {half} half of one"
        );
    }
}

#[test]
fn chorus_flanger_and_phaser_at_no_mix_are_the_input() {
    let x = noise(4000);
    for name in ["chorus", "flanger", "phaser"] {
        assert_eq!(
            run(effect(name, &[("mix", 0.0)]).as_mut(), &x, 64),
            x,
            "{name}"
        );
    }
}

#[test]
fn autopan_moves_the_sound_between_the_sides_at_its_rate() {
    let x = sine(440.0, 2.0);
    let check = |y: &[[f32; 2]], what: &str| {
        // the position is a sine of 2 Hz: hard right at 0.125 s, hard left at 0.375 s
        for k in 0..4 {
            let at = |t: f64| &y[frames(t - 0.01)..frames(t + 0.01)];
            let right = at(0.125 + 0.5 * k as f64);
            assert!(
                energy(right, 0) < 0.01 * energy(right, 1),
                "{what}: right at {k}"
            );
            let left = at(0.375 + 0.5 * k as f64);
            assert!(
                energy(left, 1) < 0.01 * energy(left, 0),
                "{what}: left at {k}"
            );
            let middle = at(0.5 * k as f64 + 0.25);
            let ratio = energy(middle, 0) / energy(middle, 1);
            assert!(
                (0.7..1.4).contains(&ratio),
                "{what}: middle at {k}: {ratio}"
            );
        }
    };
    check(
        &run(effect("autopan", &[("rate", 2.0)]).as_mut(), &x, 128),
        "2 Hz",
    );
    // a quarter note at 120 bpm is half a second
    check(
        &run(
            effect("autopan", &[("sync", 3.0), ("bpm", 120.0), ("rate", 7.0)]).as_mut(),
            &x,
            128,
        ),
        "a quarter at 120",
    );
    let y = run(
        effect("autopan", &[("rate", 2.0), ("shape", 2.0)]).as_mut(),
        &x,
        128,
    );
    let right = &y[frames(0.05)..frames(0.2)];
    assert!(energy(right, 0) < 0.01 * energy(right, 1), "square");
}

#[test]
fn doubler_adds_a_copy_each_side_at_its_delays_under_the_dry_sound() {
    let y = run(
        effect(
            "doubler",
            &[("depth", 0.0), ("level", -6.0), ("delay", 18.0)],
        )
        .as_mut(),
        &click(0.1),
        64,
    );
    let g = 10f32.powf(-6.0 / 20.0);
    assert_eq!(y[0], [1.0, 1.0]);
    let (l, r) = (frames(0.018), frames(0.027));
    assert!((y[l][0] - g).abs() < 1e-6 && y[l][1] == 0.0);
    assert!((y[r][1] - g).abs() < 1e-6 && y[r][0] == 0.0);
    let others = y
        .iter()
        .enumerate()
        .filter(|(i, _)| ![0, l, r].contains(i))
        .all(|(_, f)| *f == [0.0, 0.0]);
    assert!(others);
}

// --- all of them ---

const SPACE: &[&str] = &[
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
    "autopan",
];

#[test]
fn non_finite_input_comes_out_finite_and_does_not_stick() {
    let mut x = noise(frames(1.0));
    x[100] = [f32::NAN, f32::INFINITY];
    x[200] = [f32::NEG_INFINITY, f32::NAN];
    for name in SPACE {
        let mut e = effect(name, &[]);
        let y = run(e.as_mut(), &x, 128);
        assert!(y.iter().flatten().all(|v| v.is_finite()), "{name}");
        let later = run(e.as_mut(), &noise(frames(0.2)), 128);
        assert!(later.iter().flatten().all(|v| v.is_finite()), "{name}");
    }
}

#[test]
fn space_effects_do_not_depend_on_the_block_size() {
    let x = noise(frames(0.5));
    for name in SPACE {
        let a = run(effect(name, &[]).as_mut(), &x, 1000);
        let b = run(effect(name, &[]).as_mut(), &x, 37);
        assert_eq!(a, b, "{name}");
    }
}

#[test]
fn a_reverb_reset_back_to_a_usable_rate_sounds_as_new() {
    let x = click(0.5);
    for name in ["room", "plate", "hall", "shimmer", "gated_reverb"] {
        let fresh = run(effect(name, &[("mix", 1.0)]).as_mut(), &x, 128);
        let mut e = effect(name, &[("mix", 1.0)]);
        e.reset(10.0);
        e.reset(RATE);
        assert_eq!(run(e.as_mut(), &x, 128), fresh, "{name}");
    }
}
