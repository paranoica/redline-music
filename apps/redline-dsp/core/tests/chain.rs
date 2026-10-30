use redline_dsp_core::effect::{make, Effect};

const RATE: f32 = 44100.0;

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

fn sine(freq: f32, amp: f32, n: usize) -> Vec<[f32; 2]> {
    (0..n)
        .map(|i| {
            let v = amp * (2.0 * std::f32::consts::PI * freq * i as f32 / RATE).sin();
            [v, v]
        })
        .collect()
}

fn db(v: f64) -> f64 {
    10f64.powf(v / 20.0)
}

fn rms(x: &[[f32; 2]]) -> f64 {
    (x.iter().map(|f| (f[0] as f64).powi(2)).sum::<f64>() / x.len() as f64).sqrt()
}

/// `redline.core.dsp.compressor_gain_db`, a line-for-line offline port of the numpy law.
fn compressor_gain_db(
    x: &[[f32; 2]],
    rate: f64,
    threshold: f64,
    ratio: f64,
    attack_ms: f64,
    release_ms: f64,
    gate: f64,
) -> Vec<f64> {
    let block = ((rate * 0.001).round() as usize).max(1);
    let count = x.len().div_ceil(block);
    let blocks: Vec<f64> = (0..count)
        .map(|b| {
            let peak = x[b * block..((b + 1) * block).min(x.len())]
                .iter()
                .flat_map(|f| f.iter().map(|v| v.abs()))
                .fold(0.0f32, f32::max);
            20.0 * (peak as f64).max(1e-6).log10()
        })
        .collect();
    let mut padded = vec![blocks[0], blocks[0]];
    padded.extend(&blocks);
    padded.extend([blocks[count - 1], blocks[count - 1]]);
    let level: Vec<f64> = (0..count)
        .map(|i| {
            let mut w = [0.0; 5];
            w.copy_from_slice(&padded[i..i + 5]);
            w.sort_by(f64::total_cmp);
            w[2]
        })
        .collect();
    let attack = (-(block as f64) / rate / (attack_ms / 1000.0)).exp();
    let release = (-(block as f64) / rate / (release_ms / 1000.0)).exp();
    let (mut squash, mut expand) = (0.0f64, 0.0f64);
    let smoothed: Vec<f64> = level
        .iter()
        .map(|&l| {
            let aim_s = if l > threshold {
                (threshold - l) * (1.0 - 1.0 / ratio)
            } else {
                0.0
            };
            let aim_e = (if l < gate { l - gate } else { 0.0 }).max(-40.0);
            let c = if aim_s < squash { attack } else { release };
            squash = c * squash + (1.0 - c) * aim_s;
            let c = if aim_e > expand { attack } else { release };
            expand = c * expand + (1.0 - c) * aim_e;
            squash + expand
        })
        .collect();
    let half = (block as f64 - 1.0) / 2.0;
    (0..x.len())
        .map(|n| {
            let pos = n as f64 - half;
            if pos <= 0.0 {
                return smoothed[0];
            }
            let i = (pos / block as f64).floor() as usize;
            if i + 1 >= count {
                return smoothed[count - 1];
            }
            let t = (pos - (i * block) as f64) / block as f64;
            smoothed[i] + t * (smoothed[i + 1] - smoothed[i])
        })
        .collect()
}

/// Phrases of a voice-like tone with pauses: loud, quiet, silent, loud again.
fn phrases(n: usize) -> Vec<[f32; 2]> {
    (0..n)
        .map(|i| {
            let t = i as f32 / RATE;
            let env = match (t * 4.0) as usize % 4 {
                0 => 0.5,
                1 => 0.05,
                2 => 0.0005,
                _ => 0.3 * (1.0 + (t * 37.0).sin()) / 2.0,
            };
            let v = env * (2.0 * std::f32::consts::PI * 180.0 * t).sin();
            [v, 0.7 * v]
        })
        .collect()
}

#[test]
fn chain_compressor_follows_the_engines_law_sample_for_sample() {
    let x = phrases(2 * RATE as usize);
    let cases = [
        (-38.0, 30.0, 1.0, 40.0, -60.0, 0.0),
        (-18.0, 3.0, 10.0, 100.0, -120.0, 6.0),
        (-24.0, 6.0, 5.0, 80.0, -45.0, -3.0),
    ];
    for (threshold, ratio, attack, release, gate, makeup) in cases {
        let mut e = effect(
            "chain_compressor",
            &[
                ("threshold", threshold),
                ("ratio", ratio),
                ("attack", attack),
                ("release", release),
                ("gate", gate),
                ("makeup", makeup),
            ],
        );
        let latency = e.latency();
        assert_eq!(latency, 4 * 44, "four 1 ms blocks at 44.1 kHz");
        let mut padded = x.clone();
        padded.resize(x.len() + latency, [0.0; 2]);
        let y = run(e.as_mut(), &padded, 512);
        let gain = compressor_gain_db(
            &x,
            RATE as f64,
            threshold as f64,
            ratio as f64,
            attack as f64,
            release as f64,
            gate as f64,
        );
        // the law holds the last block's level at the end, the stream hears silence after it
        let end = x.len() - 5 * 44;
        for n in 0..end {
            let want = x[n][0] as f64 * db(gain[n] + makeup as f64);
            let got = y[n + latency][0] as f64;
            assert!(
                (got - want).abs() <= 1e-5 + 1e-4 * want.abs(),
                "case {threshold} {ratio}: frame {n}: {got} against {want}"
            );
        }
    }
}

#[test]
fn chain_compressor_squashes_over_the_threshold_and_expands_under_the_gate() {
    // a steady tone 20 dB over the threshold at 10:1 sits 18 dB down
    let x = sine(1000.0, db(-10.0) as f32, RATE as usize);
    let mut e = effect(
        "chain_compressor",
        &[("threshold", -30.0), ("ratio", 10.0), ("gate", -120.0)],
    );
    let y = run(e.as_mut(), &x, 256);
    let tail = &y[y.len() / 2..];
    let peak = tail.iter().map(|f| f[0].abs()).fold(0.0f32, f32::max) as f64;
    assert!(
        (20.0 * peak.log10() - (-28.0)).abs() < 0.3,
        "{}",
        20.0 * peak.log10()
    );
    // the same tone 30 dB under a gate is turned down by the 40 dB cap, not by 30 dB more
    let quiet = sine(1000.0, db(-70.0) as f32, RATE as usize);
    let mut e = effect("chain_compressor", &[("threshold", 0.0), ("gate", -30.0)]);
    let y = run(e.as_mut(), &quiet, 256);
    let peak = y[y.len() / 2..]
        .iter()
        .map(|f| f[0].abs())
        .fold(0.0f32, f32::max) as f64;
    assert!(
        (20.0 * peak.log10() - (-110.0)).abs() < 0.3,
        "{}",
        20.0 * peak.log10()
    );
}

#[test]
fn chain_clip_drives_and_clips_below_the_driven_peak() {
    let n = RATE as usize;
    // drive 12 dB, the loud part at -6 dBFS, clip 6 dB under its driven level
    let ceiling = (db(-6.0) * db(12.0)).tanh() * db(-6.0);
    let mut e = effect(
        "chain_clip",
        &[
            ("drive", 12.0),
            ("peak", -6.0),
            ("below", 6.0),
            ("clip", 1.0),
        ],
    );
    let y = run(e.as_mut(), &sine(200.0, 0.5, n), 512);
    let peak = y[n / 2..].iter().map(|f| f[0].abs()).fold(0.0f32, f32::max) as f64;
    assert!(
        (peak / ceiling - 1.0).abs() < 0.03,
        "{peak} against {ceiling}"
    );
    // with the clip off only the tanh is left: under 1, above the clip level
    let mut e = effect("chain_clip", &[("drive", 12.0), ("clip", 0.0)]);
    let y = run(e.as_mut(), &sine(200.0, 0.5, n), 512);
    let peak = y[n / 2..].iter().map(|f| f[0].abs()).fold(0.0f32, f32::max) as f64;
    let want = (0.5 * db(12.0)).tanh();
    assert!((peak / want - 1.0).abs() < 0.02, "{peak} against {want}");
}

#[test]
fn chain_clip_and_master_are_late_by_their_latency_and_no_more() {
    // a quiet low tone goes through nearly linear: the output is the input, latency late
    let n = RATE as usize / 2;
    let x = sine(150.0, 0.01, n);
    for (name, params) in [
        ("chain_clip", vec![("drive", 0.0), ("clip", 0.0)]),
        ("master", vec![("gain", 0.0)]),
    ] {
        let mut e = effect(name, &params);
        let latency = e.latency();
        assert!((20..=26).contains(&latency), "{name}: {latency}");
        let y = run(e.as_mut(), &x, 300);
        let err: f64 = (n / 2..n)
            .map(|i| (y[i][0] as f64 - x[i - latency][0] as f64).abs())
            .fold(0.0, f64::max);
        assert!(err < 0.01 * 0.03, "{name}: {err}");
    }
}

/// Amplitude of the `freq` component and what is left once it is taken out, in RMS.
fn component(x: &[[f32; 2]], freq: f64) -> (f64, f64) {
    let w = 2.0 * std::f64::consts::PI * freq / RATE as f64;
    let n = x.len() as f64;
    let (mut s, mut c) = (0.0, 0.0);
    for (i, f) in x.iter().enumerate() {
        s += f[0] as f64 * (w * i as f64).sin();
        c += f[0] as f64 * (w * i as f64).cos();
    }
    let (s, c) = (2.0 * s / n, 2.0 * c / n);
    let rest: f64 = x
        .iter()
        .enumerate()
        .map(|(i, f)| (f[0] as f64 - s * (w * i as f64).sin() - c * (w * i as f64).cos()).powi(2))
        .sum::<f64>()
        / n;
    (s.hypot(c), rest.sqrt())
}

#[test]
fn bass_drive_keeps_the_sub_level_and_adds_harmonics() {
    let n = 4 * RATE as usize;
    let x = sine(50.0, 0.4, n);
    let mut e = effect("bass_drive", &[("freq", 120.0), ("drive", 18.0)]);
    assert_eq!(e.latency(), 20);
    let y = run(e.as_mut(), &x, 512);
    // two whole seconds, a whole number of cycles, once the level has settled
    let (fundamental, rest) = component(&y[n / 2..], 50.0);
    // the Linkwitz-Riley low band passes 50 Hz 0.26 dB down under a 120 Hz split
    let want = 0.4 / (1.0 + (50.0f64 / 120.0).powi(4));
    assert!(
        (fundamental / want - 1.0).abs() < 0.05,
        "{fundamental} against {want}"
    );
    // 18 dB of tanh squares the tone off: its odd harmonics are well up
    assert!(rest > 0.1 * fundamental, "no harmonics: {rest}");
    // the same tone driven 6 dB harder keeps its level under 100 Hz all the same
    let mut e = effect("bass_drive", &[("freq", 120.0), ("drive", 24.0)]);
    let y = run(e.as_mut(), &x, 512);
    let (harder, more) = component(&y[n / 2..], 50.0);
    assert!(
        (harder / want - 1.0).abs() < 0.05,
        "{harder} against {want}"
    );
    assert!(more > rest, "{more} against {rest}");
}

#[test]
fn bass_drive_leaves_the_highs_and_passes_everything_at_no_drive() {
    let n = RATE as usize;
    let x = sine(3000.0, 0.3, n);
    let mut e = effect("bass_drive", &[("freq", 120.0), ("drive", 24.0)]);
    let y = run(e.as_mut(), &x, 512);
    let ratio = rms(&y[n / 2..]) / rms(&x[n / 2..]);
    assert!((20.0 * ratio.log10()).abs() < 0.1, "{ratio}");
    // at 0 dB the effect is the input, late by its latency, sample for sample
    let x = sine(60.0, 0.9, n);
    let mut e = effect("bass_drive", &[("drive", 0.0)]);
    let latency = e.latency();
    let y = run(e.as_mut(), &x, 512);
    for i in latency..n {
        assert_eq!(y[i], x[i - latency], "frame {i}");
    }
}

#[test]
fn master_raises_into_the_ceiling_and_stays_under_it() {
    let n = RATE as usize;
    let x = sine(440.0, 0.5, n);
    let ceiling = db(-0.1) as f32;
    let mut e = effect("master", &[("gain", 12.0), ("trim", 0.0)]);
    let y = run(e.as_mut(), &x, 512);
    let peak = y.iter().flatten().map(|v| v.abs()).fold(0.0f32, f32::max);
    assert!(peak <= ceiling, "{peak}");
    // squashed into the ceiling, the tone reads near it, far over its own -6 dB
    assert!(rms(&y[n / 2..]) > 0.8, "{}", rms(&y[n / 2..]));
    // a quiet tone takes the gain and the trim as they are
    let x = sine(440.0, 0.05, n);
    let mut e = effect("master", &[("gain", 6.0), ("trim", -2.0)]);
    let y = run(e.as_mut(), &x, 512);
    let ratio = rms(&y[n / 2..]) / rms(&x[n / 2..]);
    assert!((20.0 * ratio.log10() - 4.0).abs() < 0.05, "{ratio}");
}

#[test]
fn the_chain_effects_give_the_same_output_in_any_block_size() {
    let x = phrases(RATE as usize / 2);
    for name in ["chain_compressor", "chain_clip", "bass_drive", "master"] {
        let mut a = effect(name, &[]);
        let mut b = effect(name, &[]);
        let ya = run(a.as_mut(), &x, 1);
        let yb = run(b.as_mut(), &x, 777);
        assert_eq!(ya, yb, "{name}");
    }
}
