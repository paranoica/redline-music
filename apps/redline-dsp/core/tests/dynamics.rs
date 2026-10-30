mod measure;

use measure::*;
use redline_dsp_core::effect::make;

/// Gain reduction of a compressor's static curve, in dB: none under the threshold, (L - T) times
/// (1 - 1/R) over it, and the quadratic knee of Giannoulis, Massberg and Reiss in between.
fn curve(level: f32, threshold: f32, ratio: f32, knee: f32) -> f32 {
    let over = level - threshold;
    if 2.0 * over <= -knee {
        0.0
    } else if knee > 0.0 && 2.0 * over.abs() <= knee {
        (1.0 - 1.0 / ratio) * (over + knee / 2.0).powi(2) / (2.0 * knee)
    } else {
        over * (1.0 - 1.0 / ratio)
    }
}

/// Change of the steady last quarter's peak, in dB.
fn steady_change(x: &[[f32; 2]], y: &[[f32; 2]]) -> f32 {
    let from = x.len() * 3 / 4;
    db(peak(&channel(&y[from..], 0)) / peak(&channel(&x[from..], 0)))
}

#[test]
fn compressor_reduction_follows_the_ratio_over_the_threshold_in_steady_state() {
    // a square's level is its peak, whatever the detector
    for (amp, threshold, ratio) in [
        (0.5, -30.0, 4.0),
        (0.5, -20.0, 2.0),
        (0.25, -24.0, 10.0),
        (0.5, -40.0, 20.0),
        (0.02, -30.0, 4.0),
    ] {
        let x = square(100.0, amp, at(1.0));
        let params = [
            ("threshold", threshold),
            ("ratio", ratio),
            ("knee", 0.0),
            ("attack", 5.0),
            ("release", 50.0),
        ];
        let y = run(effect("compressor", &params).as_mut(), &x, 64);
        let want = -curve(db(amp), threshold, ratio, 0.0);
        let got = steady_change(&x, &y);
        assert!(
            (got - want).abs() < 0.5,
            "{amp} at {threshold} dB, {ratio}:1: {got} dB, want {want}"
        );
    }
}

#[test]
fn a_rack_hands_the_side_to_its_effects_in_blocks_of_any_length() {
    use redline_dsp_core::effect::Rack;
    let n = at(1.0);
    let quiet = sine(1000.0, 0.05, n);
    let loud = square(100.0, 0.5, n);
    let json = r#"{"effects": [{"effect": "compressor", "params":
        {"threshold": -20, "ratio": 4, "knee": 0, "detector": "side"}}]}"#;
    let mut rack = Rack::from_json(json).unwrap();
    rack.reset(RATE).unwrap();
    let mut y = quiet.clone();
    for (chunk, side) in y.chunks_mut(10000).zip(loud.chunks(10000)) {
        rack.process_with(chunk, side);
    }
    let params = [
        ("threshold", -20.0),
        ("ratio", 4.0),
        ("knee", 0.0),
        ("detector", 1.0),
    ];
    let direct = run_side(effect("compressor", &params).as_mut(), &quiet, &loud, 64);
    assert!(y == direct);
    let want = -curve(db(0.5), -20.0, 4.0, 0.0);
    assert!((steady_change(&quiet, &y) - want).abs() < 0.5);
    // a side shorter than the block is silence past its end
    let mut rack = Rack::from_json(json).unwrap();
    rack.reset(RATE).unwrap();
    let mut y = loud.clone();
    rack.process_with(&mut y, &[]);
    assert!(y == loud);
}

#[test]
fn compressor_level_is_the_rms_of_a_sine() {
    let x = sine(1000.0, 0.5, at(1.0));
    let params = [("threshold", -30.0), ("ratio", 4.0), ("knee", 0.0)];
    let y = run(effect("compressor", &params).as_mut(), &x, 64);
    let want = -curve(db(0.5 / 2f32.sqrt()), -30.0, 4.0, 0.0);
    let got = steady_change(&x, &y);
    assert!((got - want).abs() < 0.5, "{got} dB, want {want}");
}

#[test]
fn compressor_knee_and_makeup() {
    // at the threshold, a 12 dB knee at 4:1 takes (3/4) * 6^2 / 24 = 1.125 dB
    let amp = 10f32.powf(-30.0 / 20.0);
    let x = square(100.0, amp, at(1.0));
    let params = [("threshold", -30.0), ("ratio", 4.0), ("knee", 12.0)];
    let got = steady_change(&x, &run(effect("compressor", &params).as_mut(), &x, 64));
    assert!((got + 1.125).abs() < 0.2, "{got}");
    // under the threshold only the makeup acts
    let x = square(100.0, 0.01, at(1.0));
    let params = [("threshold", -20.0), ("knee", 0.0), ("makeup", 6.0)];
    let got = steady_change(&x, &run(effect("compressor", &params).as_mut(), &x, 64));
    assert!((got - 6.0).abs() < 0.05, "{got}");
}

#[test]
fn compressor_with_the_side_detector_reacts_to_the_side_only() {
    let n = at(1.0);
    let quiet = sine(1000.0, 0.05, n);
    let loud = square(100.0, 0.5, n);
    let silent = vec![[0.0f32; 2]; n];
    let params = [
        ("threshold", -20.0),
        ("ratio", 4.0),
        ("knee", 0.0),
        ("detector", 1.0),
    ];
    // a quiet main under a loud side comes down by the side's reduction
    let y = run_side(effect("compressor", &params).as_mut(), &quiet, &loud, 64);
    let want = -curve(db(0.5), -20.0, 4.0, 0.0);
    let got = steady_change(&quiet, &y);
    assert!((got - want).abs() < 0.5, "{got} dB, want {want}");
    // a loud main over a silent side, or with no side at all, is left alone
    let y = run_side(effect("compressor", &params).as_mut(), &loud, &silent, 64);
    assert!(steady_change(&loud, &y).abs() < 0.01);
    let y = run(effect("compressor", &params).as_mut(), &loud, 64);
    assert!(steady_change(&loud, &y).abs() < 0.01);
    // the input detector ignores the side
    let params = [("threshold", -20.0), ("ratio", 4.0), ("detector", 0.0)];
    let y = run_side(effect("compressor", &params).as_mut(), &quiet, &loud, 64);
    assert!(steady_change(&quiet, &y).abs() < 0.01);
}

#[test]
fn gate_holds_then_closes_to_its_range_after_hold_and_release() {
    let threshold = -40.0;
    let (hold, release) = (50.0, 100.0);
    let loud = sine(1000.0, 0.5, at(0.5));
    let quiet = sine(1000.0, 0.001, at(1.0));
    let x: Vec<[f32; 2]> = loud.iter().chain(&quiet).copied().collect();
    let last_above = x
        .iter()
        .rposition(|f| f[0].abs() > 10f32.powf(threshold / 20.0))
        .unwrap();
    for range in [100.0, 20.0] {
        let params = [
            ("threshold", threshold),
            ("attack", 1.0),
            ("hold", hold),
            ("release", release),
            ("range", range),
        ];
        let y = run(effect("gate", &params).as_mut(), &x, 64);
        let (h, r) = (at(hold / 1000.0), at(release / 1000.0));
        // open, once the first attack is over, while over and through the hold
        let open = at(0.01)..=last_above + h;
        assert!(y[open.clone()] == x[open], "range {range}");
        let floor = 10f32.powf(-range / 20.0);
        for i in last_above + h + r..x.len() {
            assert!(
                (y[i][0] - floor * x[i][0]).abs() <= 1e-6 * x[i][0].abs() + 1e-12,
                "range {range} at {i}: {} for {}",
                y[i][0],
                x[i][0]
            );
        }
        // part way through the release it is between the two
        let mid = last_above + h + r / 2;
        let g = y[mid][0] / x[mid][0];
        assert!(floor < g && g < 1.0, "{g}");
    }
}

#[test]
fn gate_opens_again_for_a_loud_sound() {
    let quiet = sine(1000.0, 0.001, at(0.5));
    let loud = sine(1000.0, 0.5, at(0.5));
    let x: Vec<[f32; 2]> = quiet.iter().chain(&loud).copied().collect();
    let params = [("threshold", -40.0), ("attack", 1.0), ("range", 100.0)];
    let y = run(effect("gate", &params).as_mut(), &x, 64);
    assert!(peak(&channel(&y[at(0.1)..at(0.5)], 0)) < 1e-7);
    let from = at(0.5) + at(0.002);
    assert!(y[from..] == x[from..]);
}

#[test]
fn deesser_takes_down_a_sibilant_burst_and_leaves_a_low_tone() {
    let n = at(1.0);
    let low = sine(300.0, 0.3, n);
    let burst = shaped(&sine(7000.0, 0.3, n), |i| {
        if (at(0.4)..at(0.7)).contains(&i) {
            1.0
        } else {
            0.0
        }
    });
    let x = add(&low, &burst);
    let params = [("frequency", 6500.0), ("threshold", -30.0), ("range", 12.0)];
    let y = run(effect("deesser", &params).as_mut(), &x, 64);
    let (a, b) = (at(0.5), at(0.7));
    let (xin, yout) = (channel(&x[a..b], 0), channel(&y[a..b], 0));
    let sibilant = db(amp_at(&yout, 7000.0) / amp_at(&xin, 7000.0));
    assert!(sibilant < -6.0, "7 kHz: {sibilant} dB");
    let tone = db(amp_at(&yout, 300.0) / amp_at(&xin, 300.0));
    assert!(tone.abs() < 0.2, "300 Hz during the burst: {tone} dB");
    // the tone alone, well after the burst, goes through as it is
    let (xin, yout) = (channel(&x[at(0.9)..], 0), channel(&y[at(0.9)..], 0));
    let tone = db(amp_at(&yout, 300.0) / amp_at(&xin, 300.0));
    assert!(tone.abs() < 0.05, "300 Hz alone: {tone} dB");
}

#[test]
fn limiter_never_passes_the_ceiling_and_limits_by_gain_not_by_clipping() {
    let ceiling = -1.0;
    let c = 10f32.powf(ceiling / 20.0);
    let n = at(1.0);
    let mut x = shaped(&sine(1000.0, 1.0, n), |i| {
        if (at(0.2)..at(0.6)).contains(&i) {
            2.0
        } else {
            0.3
        }
    });
    x[at(0.8)] = [4.0, -3.0];
    x[at(0.8) + 1] = [-4.0, 3.0];
    for (lookahead, block) in [(5.0, 64), (0.1, 7), (10.0, 512)] {
        let params = [
            ("ceiling", ceiling),
            ("lookahead", lookahead),
            ("release", 50.0),
        ];
        let mut e = effect("limiter", &params);
        let y = run(e.as_mut(), &x, block);
        let over = y.iter().flatten().fold(0.0f32, |m, v| m.max(v.abs()));
        assert!(over <= c, "lookahead {lookahead}: {over} over {c}");
        if lookahead < 1.0 {
            continue;
        }
        // a lookahead longer than the tone's period sees a peak all the time: the loud
        // steady stretch is the input delayed and scaled down by one gain
        let lag = e.latency();
        let g: Vec<f32> = (at(0.4)..at(0.55))
            .filter(|&i| x[i - lag][0].abs() > 0.5)
            .map(|i| y[i][0] / x[i - lag][0])
            .collect();
        let (lo, hi) = g
            .iter()
            .fold((f32::MAX, f32::MIN), |(a, b), v| (a.min(*v), b.max(*v)));
        assert!(hi - lo < 1e-4 && (hi - c / 2.0).abs() < 1e-3, "{lo}..{hi}");
    }
}

#[test]
fn limiter_latency_is_its_lookahead_and_exact() {
    for (lookahead, frames) in [(5.0, 240), (0.1, 5), (10.0, 480), (2.5, 120)] {
        let mut e = effect("limiter", &[("lookahead", lookahead)]);
        assert_eq!(e.latency(), frames, "{lookahead} ms");
        let mut x = vec![[0.0f32; 2]; 2000];
        x[300] = [0.5, -0.25];
        let y = run(e.as_mut(), &x, 64);
        for (i, f) in y.iter().enumerate() {
            let want = if i == 300 + frames {
                [0.5, -0.25]
            } else {
                [0.0, 0.0]
            };
            assert_eq!(*f, want, "{lookahead} ms at {i}");
        }
    }
}

#[test]
fn multiband_compresses_only_the_band_that_is_over() {
    let n = at(1.0);
    let x = add(&sine(100.0, 0.3, n), &sine(8000.0, 0.3, n));
    let tail = |y: &[[f32; 2]], f: f32| amp_at(&channel(&y[at(0.6)..], 0), f);
    // crossovers two octaves from the tones, so each tone sits inside its band
    let base = [("low", 400.0), ("high", 2000.0)];
    let with = |extra: &[(&'static str, f32)]| {
        let mut p: Vec<(&str, f32)> = base.to_vec();
        p.extend_from_slice(extra);
        run(effect("multiband", &p).as_mut(), &x, 64)
    };
    // nothing over: both tones through at their level
    let y = with(&[]);
    for f in [100.0, 8000.0] {
        let change = db(tail(&y, f) / 0.3);
        assert!(change.abs() < 0.2, "{f} Hz: {change}");
    }
    // the high band over its threshold
    let y = with(&[("high_threshold", -30.0), ("high_ratio", 4.0)]);
    let low = db(tail(&y, 100.0) / 0.3);
    let high = db(tail(&y, 8000.0) / 0.3);
    let want = -curve(db(0.3 / 2f32.sqrt()), -30.0, 4.0, 0.0);
    assert!(low.abs() < 0.2, "low {low}");
    assert!((high - want).abs() < 1.0, "high {high}, want {want}");
    // the low band over its threshold
    let y = with(&[("low_threshold", -30.0), ("low_ratio", 4.0)]);
    let low = db(tail(&y, 100.0) / 0.3);
    let high = db(tail(&y, 8000.0) / 0.3);
    assert!((low - want).abs() < 1.0, "low {low}, want {want}");
    assert!(high.abs() < 0.2, "high {high}");
}

#[test]
fn duck_lowers_the_main_by_its_depth_while_the_side_is_loud() {
    let n = at(2.0);
    let main = sine(1000.0, 0.3, n);
    let side = shaped(&sine(60.0, 0.5, n), |i| {
        if (at(0.5)..at(1.0)).contains(&i) {
            1.0
        } else {
            0.0
        }
    });
    let params = [
        ("threshold", -30.0),
        ("depth", 12.0),
        ("attack", 5.0),
        ("release", 100.0),
    ];
    let y = run_side(effect("duck", &params).as_mut(), &main, &side, 64);
    let level = |a: f32, b: f32| db(peak(&channel(&y[at(a)..at(b)], 0)) / 0.3);
    assert!(level(0.2, 0.5).abs() < 0.01, "before: {}", level(0.2, 0.5));
    let during = level(0.7, 1.0);
    assert!((during + 12.0).abs() < 0.5, "during: {during}");
    assert!(level(1.8, 2.0).abs() < 0.1, "after: {}", level(1.8, 2.0));
    // no side, no ducking
    let y = run(effect("duck", &params).as_mut(), &main, 64);
    assert!(y == main);
}

/// A plucked 220 Hz tone: sharp onset at 0.1 s, then an exponential decay.
fn pluck() -> Vec<[f32; 2]> {
    let onset = at(0.1);
    shaped(&sine(220.0, 0.5, at(0.8)), |i| {
        if i < onset {
            0.0
        } else {
            (-((i - onset) as f32 / RATE) / 0.15).exp()
        }
    })
}

fn window_db(x: &[[f32; 2]], y: &[[f32; 2]], from: f32, to: f32) -> f32 {
    let (a, b) = (at(from), at(to));
    db(rms(&channel(&y[a..b], 0)) / rms(&channel(&x[a..b], 0)))
}

#[test]
fn transient_shaper_raises_or_lowers_the_attack_of_a_pluck() {
    let x = pluck();
    let up = run(effect("transient", &[("attack", 12.0)]).as_mut(), &x, 64);
    let down = run(effect("transient", &[("attack", -12.0)]).as_mut(), &x, 64);
    let (a_up, a_down) = (
        window_db(&x, &up, 0.1, 0.11),
        window_db(&x, &down, 0.1, 0.11),
    );
    assert!(a_up > 4.0, "attack up: {a_up}");
    assert!(a_down < -4.0, "attack down: {a_down}");
    // the tail long after the onset is left nearly alone
    for (y, what) in [(&up, "up"), (&down, "down")] {
        let tail = window_db(&x, y, 0.4, 0.6);
        assert!(tail.abs() < 1.5, "tail with attack {what}: {tail}");
    }
    let neutral = run(effect("transient", &[]).as_mut(), &x, 64);
    assert!(neutral == x);
}

#[test]
fn transient_shaper_sustain_raises_the_tail() {
    let x = pluck();
    let y = run(effect("transient", &[("sustain", 12.0)]).as_mut(), &x, 64);
    let tail = window_db(&x, &y, 0.3, 0.6);
    let onset = window_db(&x, &y, 0.1, 0.103);
    assert!(tail > 3.0, "tail {tail}");
    assert!(tail > onset + 2.0, "tail {tail}, onset {onset}");
}

/// Hits of a 220 Hz tone every quarter second, each dying away over 30 ms.
fn hits(rate: f32) -> Vec<[f32; 2]> {
    (0..(2.0 * rate) as usize)
        .map(|i| {
            let u = (i as f32 / rate) % 0.25;
            let v = (2.0 * std::f32::consts::PI * 220.0 * u).sin() * (-u / 0.03).exp();
            [v, v]
        })
        .collect()
}

fn transient_peak(rate: f32, x: &[[f32; 2]]) -> f32 {
    let mut e = make("transient").unwrap();
    e.set("attack", 24.0).unwrap();
    e.set("sustain", 24.0).unwrap();
    e.reset(rate);
    let y = run(e.as_mut(), x, 512);
    y.iter().flatten().fold(0.0f32, |m, v| m.max(v.abs()))
}

#[test]
fn transient_peaks_agree_across_rates_and_an_onset_is_not_also_a_decay() {
    let at_48k = transient_peak(48000.0, &hits(48000.0));
    let at_384k = transient_peak(384000.0, &hits(384000.0));
    assert!(
        db(at_384k / at_48k).abs() < 3.0,
        "{at_48k} against {at_384k}"
    );
    // the attack and the sustain never raise the same sample together: a full-scale input
    // comes out at most 24 dB up
    for rate in [8000.0, 48000.0, 384000.0] {
        let top = transient_peak(rate, &hits(rate));
        assert!(top <= 10f32.powf(24.0 / 20.0) * 1.001, "{rate} Hz: {top}");
    }
}
