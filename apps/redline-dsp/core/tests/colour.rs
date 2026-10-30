mod measure;

use measure::*;
use redline_dsp_core::effect::{make, EFFECTS};
use std::f64::consts::PI;

/// The new effects of the dynamics and colour waves.
const NEW: &[&str] = &[
    "compressor",
    "gate",
    "deesser",
    "limiter",
    "multiband",
    "duck",
    "transient",
    "distort",
    "downsample",
    "telephone",
    "radio",
    "lofi",
    "underwater",
    "whisper",
    "vocoder",
    "talkbox",
    "ring_mod",
    "freq_shift",
    "comb",
    "resonator",
    "exciter",
];

fn tail_amp(y: &[[f32; 2]], freq: f32) -> f32 {
    amp_at(&channel(&y[y.len() / 2..], 0), freq)
}

/// |H(f)| of an impulse response: its plain DFT, no window.
fn gain_at(h: &[f32], freq: f32) -> f32 {
    let w = 2.0 * PI * freq as f64 / RATE as f64;
    let (mut re, mut im) = (0.0f64, 0.0f64);
    for (i, &v) in h.iter().enumerate() {
        re += v as f64 * (w * i as f64).cos();
        im -= v as f64 * (w * i as f64).sin();
    }
    re.hypot(im) as f32
}

/// On for 0.2-0.6 s and 0.8-1.2 s, silent elsewhere, over 1.3 s.
fn gated(x: &[[f32; 2]]) -> Vec<[f32; 2]> {
    shaped(x, |i| {
        let on = (at(0.2)..at(0.6)).contains(&i) || (at(0.8)..at(1.2)).contains(&i);
        if on {
            1.0
        } else {
            0.0
        }
    })
}

fn on_off_db(y: &[[f32; 2]]) -> (f32, f32) {
    let on = rms(&channel(&y[at(0.3)..at(0.55)], 0));
    let off = rms(&channel(&y[at(0.68)..at(0.78)], 0));
    (db(on), db(off))
}

#[test]
fn every_new_effect_is_registered() {
    for name in NEW {
        assert!(EFFECTS.contains(name), "{name}");
        assert!(make(name).is_some(), "{name}");
    }
}

#[test]
fn every_new_effect_is_deterministic_whatever_the_blocks() {
    let x = add(&sine(220.0, 0.3, at(0.5)), &noise(at(0.5), 0.1, 3));
    let side = saw(110.0, 0.4, at(0.5));
    for name in NEW {
        let a = run_side(effect(name, &[]).as_mut(), &x, &side, 64);
        let b = run_side(effect(name, &[]).as_mut(), &x, &side, 333);
        assert!(a == b, "{name}");
        assert!(a.iter().flatten().all(|v| v.is_finite()), "{name}");
    }
}

#[test]
fn every_new_effect_cleans_non_finite_input_and_side() {
    let mut x = sine(220.0, 0.3, at(0.3));
    x[100] = [f32::NAN, 0.1];
    x[2000] = [f32::INFINITY, f32::NEG_INFINITY];
    let mut side = saw(110.0, 0.4, at(0.3));
    side[150] = [f32::NAN, f32::INFINITY];
    for name in NEW {
        let y = run_side(effect(name, &[]).as_mut(), &x, &side, 64);
        assert!(y.iter().flatten().all(|v| v.is_finite()), "{name}");
        let late = peak(&channel(&y[at(0.25)..], 0));
        assert!(late.is_finite(), "{name}");
    }
}

#[test]
fn distort_adds_harmonics_by_mode() {
    let x = sine(200.0, 0.5, at(0.5));
    let harmonic = |y: &[[f32; 2]], k: f32| db(tail_amp(y, 200.0 * k) / tail_amp(y, 200.0));
    // modes: hard, fold, tube
    let hard = run(
        effect("distort", &[("mode", 0.0), ("drive", 12.0)]).as_mut(),
        &x,
        64,
    );
    assert!(harmonic(&hard, 3.0) > -30.0, "{}", harmonic(&hard, 3.0));
    assert!(harmonic(&hard, 2.0) < -60.0, "{}", harmonic(&hard, 2.0));
    let fold = run(
        effect("distort", &[("mode", 1.0), ("drive", 12.0)]).as_mut(),
        &x,
        64,
    );
    assert!(harmonic(&fold, 3.0) > -30.0, "{}", harmonic(&fold, 3.0));
    let tube = run(
        effect("distort", &[("mode", 2.0), ("drive", 12.0)]).as_mut(),
        &x,
        64,
    );
    assert!(harmonic(&tube, 2.0) > -40.0, "{}", harmonic(&tube, 2.0));
    // nothing driven past the edge: no harmonics; no mix: the input as it is
    let clean = run(
        effect(
            "distort",
            &[("mode", 0.0), ("drive", 0.0), ("tone", 20000.0)],
        )
        .as_mut(),
        &x,
        64,
    );
    assert!(harmonic(&clean, 3.0) < -80.0, "{}", harmonic(&clean, 3.0));
    let dry = run(effect("distort", &[("mix", 0.0)]).as_mut(), &x, 64);
    assert!(dry == x);
}

#[test]
fn downsample_holds_samples_and_leaves_the_images() {
    let x = sine(1000.0, 0.5, at(0.5));
    let y = run(effect("downsample", &[("rate", 4000.0)]).as_mut(), &x, 64);
    // 48 kHz to 4 kHz: each value held for 12 frames
    let changes = y.windows(2).filter(|w| w[0] != w[1]).count();
    assert!((changes as f32 - 0.5 * 4000.0).abs() <= 2.0, "{changes}");
    let image = db(tail_amp(&y, 3000.0) / tail_amp(&y, 1000.0));
    assert!(image > -20.0, "the 3 kHz image: {image} dB");
}

#[test]
fn telephone_keeps_300_to_3400_hz() {
    for (freq, pass) in [(1000.0, true), (100.0, false), (10000.0, false)] {
        let x = sine(freq, 0.1, at(0.5));
        let y = run(effect("telephone", &[]).as_mut(), &x, 64);
        let change = db(tail_amp(&y, freq) / 0.1);
        if pass {
            assert!(change.abs() < 1.0, "{freq} Hz: {change}");
        } else {
            assert!(change < -30.0, "{freq} Hz: {change}");
        }
    }
}

#[test]
fn radio_keeps_500_to_4000_hz_over_a_seeded_noise_floor() {
    for (freq, pass) in [(1500.0, true), (200.0, false), (10000.0, false)] {
        let x = sine(freq, 0.1, at(0.5));
        let y = run(effect("radio", &[("noise", -90.0)]).as_mut(), &x, 64);
        let change = db(tail_amp(&y, freq) / 0.1);
        if pass {
            assert!(change.abs() < 1.5, "{freq} Hz: {change}");
        } else {
            assert!(change < -20.0, "{freq} Hz: {change}");
        }
    }
    let silence = vec![[0.0f32; 2]; at(0.5)];
    let a = run(effect("radio", &[("noise", -50.0)]).as_mut(), &silence, 64);
    let floor = db(rms(&channel(&a, 0)));
    assert!(-80.0 < floor && floor < -45.0, "{floor}");
    let again = run(effect("radio", &[("noise", -50.0)]).as_mut(), &silence, 64);
    let other = run(
        effect("radio", &[("noise", -50.0), ("seed", 7.0)]).as_mut(),
        &silence,
        64,
    );
    assert!(a == again && a != other);
}

#[test]
fn lofi_quantises_and_reports_its_delay() {
    let x = sine(440.0, 0.5, at(0.5));
    let clean = [
        ("bits", 16.0),
        ("rate", 48000.0),
        ("wow", 0.0),
        ("flutter", 0.0),
        ("noise", -90.0),
    ];
    let mut e = effect("lofi", &clean);
    let lag = e.latency();
    assert!(lag > 0);
    let y = run(e.as_mut(), &x, 64);
    let noise = 10f32.powf(-90.0 / 20.0);
    for i in lag..x.len() {
        let err = (y[i][0] - x[i - lag][0]).abs();
        assert!(err <= 0.5 / 32768.0 + noise + 1e-6, "{i}: {err}");
    }
    // four bits: steps of an eighth, before the noise
    let crushed = [
        ("bits", 4.0),
        ("wow", 0.0),
        ("flutter", 0.0),
        ("noise", -90.0),
    ];
    let y = run(effect("lofi", &crushed).as_mut(), &x, 64);
    for f in &y[lag..] {
        let off = (f[0] * 8.0 - (f[0] * 8.0).round()).abs() / 8.0;
        assert!(off <= noise + 1e-6, "{}", f[0]);
    }
    let a = run(effect("lofi", &[]).as_mut(), &x, 64);
    let other = run(effect("lofi", &[("seed", 9.0)]).as_mut(), &x, 64);
    assert!(a != other);
}

#[test]
fn underwater_is_a_low_pass() {
    let still = [("cutoff", 500.0), ("depth", 0.0), ("chorus", 0.0)];
    let low = sine(100.0, 0.3, at(0.5));
    let high = sine(5000.0, 0.3, at(0.5));
    let y = run(effect("underwater", &still).as_mut(), &low, 64);
    assert!(db(tail_amp(&y, 100.0) / 0.3).abs() < 1.0);
    let y = run(effect("underwater", &still).as_mut(), &high, 64);
    assert!(db(tail_amp(&y, 5000.0) / 0.3) < -30.0);
    // moving, the cutoff sweeps: the level of a tone near it changes over time
    let x = sine(700.0, 0.3, at(4.0));
    let y = run(
        effect(
            "underwater",
            &[("depth", 1.0), ("speed", 1.0), ("chorus", 0.0)],
        )
        .as_mut(),
        &x,
        64,
    );
    let levels: Vec<f32> = y
        .chunks(at(0.1))
        .skip(2)
        .map(|c| db(rms(&channel(c, 0))))
        .collect();
    let (lo, hi) = levels
        .iter()
        .fold((f32::MAX, f32::MIN), |(a, b), v| (a.min(*v), b.max(*v)));
    assert!(hi - lo > 6.0, "{lo}..{hi}");
}

#[test]
fn whisper_has_no_pitch_and_follows_the_envelope() {
    let x = gated(&saw(150.0, 0.3, at(1.3)));
    // YIN starts primed with silence: its first few detections see only part of the sound
    let voiced = |y: &[[f32; 2]]| {
        let mut found = pitches(&channel(&y[at(0.25)..at(0.55)], 0)).split_off(5);
        found.extend(pitches(&channel(&y[at(0.85)..at(1.15)], 0)).split_off(5));
        found
    };
    // the input is clearly pitched
    assert!(share_at(&voiced(&x), 150.0, 30.0) > 0.9);
    let y = run(effect("whisper", &[]).as_mut(), &x, 64);
    let found = voiced(&y);
    let pitched = found.iter().filter(|f| f.is_some()).count();
    assert!(
        pitched as f32 <= 0.05 * found.len() as f32,
        "{pitched} of {}",
        found.len()
    );
    let (on, off) = on_off_db(&y);
    let (x_on, _) = on_off_db(&x);
    assert!(on - off > 30.0, "on {on}, off {off}");
    assert!((on - x_on).abs() < 6.0, "on {on}, input {x_on}");
}

/// A carrier at A3, one note: key A, minor, first degree, octave 3.
const A3: &[(&str, f32)] = &[
    ("key", 9.0),
    ("scale", 1.0),
    ("degree", 1.0),
    ("notes", 1.0),
    ("octave", 3.0),
];

fn carrier_test(name: &str) {
    let x = gated(&noise(at(1.3), 0.3, 11));
    let pitched = |y: &[[f32; 2]], f: f32| {
        let mut found = pitches(&channel(&y[at(0.25)..at(0.55)], 0)).split_off(5);
        found.extend(pitches(&channel(&y[at(0.85)..at(1.15)], 0)).split_off(5));
        share_at(&found, f, 30.0)
    };
    let y = run(effect(name, A3).as_mut(), &x, 64);
    let share = pitched(&y, 220.0);
    assert!(share > 0.7, "{name}: {share} at 220 Hz");
    let (on, off) = on_off_db(&y);
    assert!(on - off > 30.0, "{name}: on {on}, off {off}");
    assert!(on > -30.0, "{name}: on {on}");
    // the side as the carrier: its pitch instead
    let mut p = A3.to_vec();
    p.push(("carrier", 2.0));
    let side = clean_saw(330.0, 0.4, at(1.3));
    let y = run_side(effect(name, &p).as_mut(), &x, &side, 64);
    let share = pitched(&y, 330.0);
    assert!(share > 0.7, "{name}: {share} at 330 Hz");
    // no side: nothing to carry the sound
    let y = run(effect(name, &p).as_mut(), &x, 64);
    assert!(peak(&channel(&y, 0)) < 1e-6, "{name}");
}

#[test]
fn vocoder_follows_the_input_envelope_at_the_carrier_pitch() {
    carrier_test("vocoder");
}

#[test]
fn talkbox_follows_the_input_envelope_at_the_carrier_pitch() {
    carrier_test("talkbox");
}

#[test]
fn talkbox_and_vocoder_impose_the_input_spectral_envelope() {
    let x = noise(at(1.0), 0.3, 5);
    let filtered = |mode: f32, cutoff: f32| {
        let mut f = effect("filter", &[("mode", mode), ("cutoff", cutoff)]);
        run(f.as_mut(), &x, 64)
    };
    let dark = filtered(0.0, 400.0);
    let bright = filtered(1.0, 3000.0);
    // energy over 2 kHz against under it, by the 1 kHz-wide view of a few harmonics
    let tilt = |y: &[[f32; 2]]| {
        let ch = channel(&y[at(0.3)..], 0);
        let low: f32 = [220.0, 440.0, 660.0].iter().map(|f| amp_at(&ch, *f)).sum();
        let high: f32 = [3080.0, 3300.0, 3520.0]
            .iter()
            .map(|f| amp_at(&ch, *f))
            .sum();
        db(high / low)
    };
    for name in ["vocoder", "talkbox"] {
        let d = tilt(&run(effect(name, A3).as_mut(), &dark, 64));
        let b = tilt(&run(effect(name, A3).as_mut(), &bright, 64));
        assert!(b - d > 20.0, "{name}: dark {d}, bright {b}");
    }
}

#[test]
fn ring_mod_gives_the_sum_and_difference() {
    let x = sine(1000.0, 0.5, at(0.5));
    let y = run(effect("ring_mod", &[("frequency", 300.0)]).as_mut(), &x, 64);
    for f in [700.0, 1300.0] {
        let a = tail_amp(&y, f);
        assert!((db(a / 0.25)).abs() < 0.2, "{f}: {a}");
    }
    assert!(tail_amp(&y, 1000.0) < 1e-3);
    assert!(tail_amp(&y, 300.0) < 1e-3);
}

#[test]
fn freq_shift_moves_a_sine_by_hertz_not_by_a_ratio() {
    for (input, shift) in [
        (1000.0, 100.0),
        (500.0, 100.0),
        (1000.0, -250.0),
        (3000.0, 1000.0),
    ] {
        let x = sine(input, 0.5, at(0.5));
        let y = run(effect("freq_shift", &[("shift", shift)]).as_mut(), &x, 64);
        let ch = channel(&y[at(0.1)..], 0);
        let moved = input + shift;
        assert!(
            db(amp_at(&ch, moved) / 0.5).abs() < 0.5,
            "{input} + {shift}"
        );
        for other in [input, input - shift] {
            let left = db(amp_at(&ch, other) / 0.5);
            assert!(left < -30.0, "{input} + {shift}: {left} dB at {other}");
        }
    }
}

#[test]
fn comb_peaks_at_multiples_of_one_over_the_delay() {
    let mut x = vec![[0.0f32; 2]; at(1.0)];
    x[0] = [1.0, 1.0];
    let params = [("delay", 5.0), ("feedback", 0.8), ("mix", 1.0)];
    let h = channel(&run(effect("comb", &params).as_mut(), &x, 64), 0);
    for k in 1..=5 {
        let top = db(gain_at(&h, 200.0 * k as f32));
        let dip = db(gain_at(&h, 200.0 * k as f32 + 100.0));
        assert!(top.abs() < 0.5, "{k}: {top}");
        assert!(dip < -15.0, "{k}: {dip}");
    }
    let params = [("delay", 3.3), ("feedback", 0.8), ("mix", 1.0)];
    let h = channel(&run(effect("comb", &params).as_mut(), &x, 64), 0);
    let mut best = (0.0, 0.0);
    let mut f = 250.0;
    while f < 350.0 {
        let g = gain_at(&h, f);
        if g > best.0 {
            best = (g, f);
        }
        f += 0.5;
    }
    assert!((best.1 - 1000.0 / 3.3f32).abs() < 3.0, "{}", best.1);
}

#[test]
fn resonator_rings_at_its_notes() {
    // C major, first degree, three notes, octave 4: C4 E4 G4
    let notes = [261.63, 329.63, 392.0];
    let burst = shaped(&noise(at(1.0), 0.5, 4), |i| {
        if i < at(0.01) {
            1.0
        } else {
            0.0
        }
    });
    let params = [
        ("key", 0.0),
        ("scale", 0.0),
        ("degree", 1.0),
        ("notes", 3.0),
        ("octave", 4.0),
        ("decay", 2.0),
        ("mix", 1.0),
    ];
    let y = run(effect("resonator", &params).as_mut(), &burst, 64);
    let ring = channel(&y[at(0.05)..at(0.55)], 0);
    let level = |f: f32| amp_at(&ring, f);
    let floor = [230.0, 295.0, 360.0, 425.0]
        .iter()
        .map(|f| level(*f))
        .fold(0.0, f32::max);
    for f in notes {
        assert!(
            db(level(f) / floor) > 20.0,
            "{f}: {} over {floor}",
            level(f)
        );
    }
    // a decay of 2 s is 60 dB in 2 s: 15 dB over half a second
    let early = amp_at(&channel(&y[at(0.1)..at(0.3)], 0), notes[0]);
    let late = amp_at(&channel(&y[at(0.6)..at(0.8)], 0), notes[0]);
    let fall = db(early / late);
    assert!((fall - 15.0).abs() < 4.0, "{fall}");
}

#[test]
fn exciter_adds_harmonics_above_its_frequency_only() {
    let params = [("frequency", 3000.0), ("amount", 0.5)];
    let high = sine(4000.0, 0.3, at(0.5));
    let y = run(effect("exciter", &params).as_mut(), &high, 64);
    let added = db((tail_amp(&y, 8000.0) + tail_amp(&y, 12000.0)) / 0.3);
    assert!(added > -40.0, "{added}");
    let low = sine(500.0, 0.3, at(0.5));
    let y = run(effect("exciter", &params).as_mut(), &low, 64);
    for k in [2.0, 3.0] {
        let h = db(tail_amp(&y, 500.0 * k) / 0.3);
        assert!(h < -80.0, "{k}: {h}");
    }
    let y = run(effect("exciter", &[("amount", 0.0)]).as_mut(), &high, 64);
    assert!(y == high);
}
