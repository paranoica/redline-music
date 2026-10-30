//! The pitch effects on generated sound: what comes out is measured, not compared to a capture.

use redline_dsp_core::effect::{make, Effect, Rack};
use redline_dsp_core::pitch::{freq, midi, Scale, Yin};
use redline_dsp_core::stretch::Stretch;
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

/// A tone whose frequency follows `f(t)`, the phase integrated so glides stay smooth.
fn tone(secs: f32, amp: f32, f: impl Fn(f64) -> f64) -> Vec<[f32; 2]> {
    let n = (secs * RATE) as usize;
    let mut phase = 0.0f64;
    (0..n)
        .map(|i| {
            let v = (amp as f64 * phase.sin()) as f32;
            phase += 2.0 * PI * f(i as f64 / RATE as f64) / RATE as f64;
            [v, v]
        })
        .collect()
}

fn sine(hz: f64, secs: f32) -> Vec<[f32; 2]> {
    tone(secs, 0.5, |_| hz)
}

fn channel(y: &[[f32; 2]], ch: usize) -> Vec<f32> {
    y.iter().map(|f| f[ch]).collect()
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

/// Hann-windowed magnitude spectrum, zero padded to a power of two; bin width `RATE / n`.
struct Spectrum {
    mag: Vec<f64>,
    n: usize,
}

impl Spectrum {
    fn of(x: &[f32]) -> Spectrum {
        let n = (x.len() * 2).next_power_of_two();
        let mut re = vec![0.0; n];
        let mut im = vec![0.0; n];
        let m = x.len() as f64;
        for (i, v) in x.iter().enumerate() {
            re[i] = *v as f64 * (0.5 - 0.5 * (2.0 * PI * i as f64 / m).cos());
        }
        fft(&mut re, &mut im);
        let mag = (0..n / 2).map(|k| re[k].hypot(im[k])).collect();
        Spectrum { mag, n }
    }

    fn bin(&self, hz: f64) -> usize {
        (hz * self.n as f64 / RATE as f64).round() as usize
    }

    /// The strongest frequency between `lo` and `hi`, interpolated on log magnitude.
    fn peak(&self, lo: f64, hi: f64) -> f64 {
        let (a, b) = (self.bin(lo), self.bin(hi));
        let k = (a..=b)
            .max_by(|&i, &j| self.mag[i].total_cmp(&self.mag[j]))
            .unwrap();
        let l = |i: usize| self.mag[i].max(1e-30).ln();
        let (p, q, r) = (l(k - 1), l(k), l(k + 1));
        let shift = 0.5 * (p - r) / (p - 2.0 * q + r);
        (k as f64 + shift) * RATE as f64 / self.n as f64
    }

    /// The largest magnitude within `width` Hz of `hz`.
    fn level(&self, hz: f64, width: f64) -> f64 {
        (self.bin(hz - width)..=self.bin(hz + width))
            .map(|k| self.mag[k])
            .fold(0.0, f64::max)
    }
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

/// Frame frequencies of channel 0 from `start` seconds on, in frames of `frame` seconds.
fn track(y: &[[f32; 2]], start: f32, frame: f32) -> Vec<f64> {
    let x = channel(y, 0);
    let (s, w) = ((start * RATE) as usize, (frame * RATE) as usize);
    x[s.min(x.len())..]
        .chunks_exact(w)
        .filter_map(crossing_freq)
        .collect()
}

fn cents(f: f64, reference: f64) -> f64 {
    1200.0 * (f / reference).log2()
}

/// Mean square of a channel from `start` seconds on.
fn power(x: &[f32], start: f32) -> f64 {
    let x = &x[(start * RATE) as usize..];
    x.iter().map(|v| (*v as f64).powi(2)).sum::<f64>() / x.len() as f64
}

fn steady(y: &[[f32; 2]], from: f32) -> Spectrum {
    Spectrum::of(&channel(y, 0)[(from * RATE) as usize..])
}

// --- detector and scales ---

#[test]
fn yin_finds_the_pitch_of_a_sine_and_nothing_in_silence_or_noise() {
    for hz in [82.41, 220.0, 450.0, 1000.0] {
        let mut yin = Yin::new();
        yin.reset(RATE);
        let mut got = Vec::new();
        for f in sine(hz, 0.5) {
            if yin.push(f[0]) {
                got.extend(yin.freq());
            }
        }
        // once the window is full of tone, every detection is the tone
        let settled = &got[got.len() - 20..];
        assert!(
            settled
                .iter()
                .all(|f| (f - hz as f32).abs() < 0.05 * hz as f32 / 100.0),
            "{hz}: {settled:?}"
        );
    }
    let mut yin = Yin::new();
    yin.reset(RATE);
    let mut voiced = 0;
    for i in 0..RATE as usize {
        let noise = (((i as f64) * 12.9898).sin() * 43758.5453).fract() as f32 * 0.5;
        let x = if i < RATE as usize / 2 { 0.0 } else { noise };
        if yin.push(x) && yin.freq().is_some() {
            voiced += 1;
        }
    }
    assert!(voiced < 5, "{voiced} noise or silence frames called voiced");
}

#[test]
fn notes_and_frequencies_convert_both_ways() {
    assert!((midi(440.0, 0.0) - 69.0).abs() < 1e-5);
    assert!((midi(261.6256, 0.0) - 60.0).abs() < 1e-4);
    assert!((freq(69.0, 0.0) - 440.0).abs() < 1e-3);
    // A4 tuned 20 cents up: 440 Hz is then 20 cents under A4
    assert!((midi(440.0, 20.0) - 68.8).abs() < 1e-4);
    assert!((freq(69.0, 20.0) - 440.0 * 2f32.powf(20.0 / 1200.0)).abs() < 1e-3);
}

#[test]
fn a_scale_snaps_to_its_own_notes_and_counts_intervals_in_degrees() {
    let c_major = Scale::new(0, 0);
    assert_eq!(c_major.nearest(60.2), 60);
    assert_eq!(c_major.nearest(61.4), 62); // C# is not in C major: D is nearer than C
    assert_eq!(c_major.nearest(65.6), 65);
    assert_eq!(c_major.nearest(47.9), 48);
    assert_eq!(c_major.interval(60, 2), 4); // C up a third: E
    assert_eq!(c_major.interval(60, 4), 7); // C up a fifth: G
    assert_eq!(c_major.interval(62, 2), 3); // D up a third: F, a minor third
    assert_eq!(c_major.interval(71, 1), 1); // B to C
    assert_eq!(c_major.interval(60, -1), -1); // C down to B
    assert_eq!(c_major.interval(60, 7), 12);
    assert_eq!(c_major.interval(60, -7), -12);
    let a_minor_harmonic = Scale::new(9, 2);
    assert_eq!(a_minor_harmonic.nearest(67.2), 68); // G# instead of G
    let chromatic = Scale::new(9, 5);
    assert_eq!(chromatic.nearest(69.4), 69);
    assert_eq!(chromatic.interval(69, 3), 3);
    let e_phrygian = Scale::new(4, 3);
    assert_eq!(e_phrygian.interval(64, 1), 1); // E to F
    let d_dorian = Scale::new(2, 4);
    assert_eq!(d_dorian.interval(62, 5), 9); // D to B
}

// --- the stretch bridge ---

#[test]
fn the_stretch_bridge_shifts_a_sine_and_reports_latency() {
    let mut s = Stretch::new(2, RATE, 1);
    assert!(s.latency() > 0 && s.input_latency() > 0 && s.input_latency() < s.latency());
    s.set_transpose(1.5);
    let x = sine(400.0, 1.0);
    let mut y = vec![[0.0f32; 2]; x.len()];
    for (a, b) in x.chunks(100).zip(y.chunks_mut(100)) {
        s.process(a.as_flattened(), b.as_flattened_mut());
    }
    let got = steady(&y, 0.4).peak(300.0, 900.0);
    assert!((got - 600.0).abs() < 3.0, "{got}");
    s.reset();
    s.set_transpose(1.0);
    let mut y = vec![[0.0f32; 2]; x.len()];
    s.process(x.as_flattened(), y.as_flattened_mut());
    let got = steady(&y, 0.4).peak(300.0, 900.0);
    assert!((got - 400.0).abs() < 2.0, "{got}");
}

// --- pitch and formant ---

#[test]
fn pitch_up_an_octave_doubles_the_fundamental_with_or_without_formants() {
    for preserve in [0.0, 1.0] {
        let mut e = effect("pitch", &[("semitones", 12.0), ("preserve", preserve)]);
        assert!(e.latency() > 0);
        let y = run(e.as_mut(), &sine(440.0, 1.5), 256);
        let got = steady(&y, 0.5).peak(200.0, 2000.0);
        assert!(
            (got / 880.0 - 1.0).abs() < 0.01,
            "preserve {preserve}: {got}"
        );
    }
    let mut e = effect("pitch", &[("semitones", -12.0)]);
    let got = steady(&run(e.as_mut(), &sine(440.0, 1.5), 64), 0.5).peak(100.0, 1000.0);
    assert!((got / 220.0 - 1.0).abs() < 0.01, "{got}");
    let mut e = effect("pitch", &[("semitones", 0.0), ("cents", 50.0)]);
    let got = steady(&run(e.as_mut(), &sine(440.0, 1.5), 512), 0.5).peak(300.0, 600.0);
    // signalsmith places a shifted sine within about 5 cents of its target
    assert!((cents(got, 440.0) - 50.0).abs() < 6.0, "{got}");
    let mut e = effect("pitch", &[]);
    let got = steady(&run(e.as_mut(), &sine(440.0, 1.5), 512), 0.5).peak(300.0, 600.0);
    assert!((got - 440.0).abs() < 0.5, "{got}");
}

/// A sung "a": harmonics of 150 Hz shaped by formant peaks near 700, 1200 and 2600 Hz.
fn vowel(secs: f32) -> Vec<[f32; 2]> {
    let n = (secs * RATE) as usize;
    let formants = [(700.0, 90.0), (1200.0, 110.0), (2600.0, 160.0)];
    let harmonics: Vec<(f64, f64)> = (1..33)
        .map(|k| {
            let f = 150.0 * k as f64;
            let env: f64 = formants
                .iter()
                .map(|(c, b)| 1.0 / (1.0 + ((f - c) / b).powi(2)))
                .sum();
            (f, 0.02 + env)
        })
        .collect();
    (0..n)
        .map(|i| {
            let t = i as f64 / RATE as f64;
            let v: f64 = harmonics
                .iter()
                .enumerate()
                .map(|(k, (f, a))| a * (2.0 * PI * f * t + 0.7 * (k * k) as f64).sin())
                .sum();
            let v = (0.1 * v) as f32;
            [v, v]
        })
        .collect()
}

/// Amplitude-weighted mean frequency of the harmonics of 150 Hz up to 4.5 kHz.
fn envelope_centroid(s: &Spectrum) -> f64 {
    let (mut num, mut den) = (0.0, 0.0);
    for k in 1..31 {
        let f = 150.0 * k as f64;
        let a = s.level(f, 20.0);
        num += f * a;
        den += a;
    }
    num / den
}

#[test]
fn formant_moves_the_spectral_envelope_and_keeps_the_fundamental() {
    let x = vowel(2.0);
    let before = envelope_centroid(&steady(&x, 0.6));
    let mut measured = Vec::new();
    for shift in [-4.0, 0.0, 4.0] {
        let mut e = effect("formant", &[("semitones", shift)]);
        let y = run(e.as_mut(), &x, 256);
        let s = steady(&y, 0.6);
        let f0 = s.peak(100.0, 200.0);
        assert!(
            (f0 / 150.0 - 1.0).abs() < 0.01,
            "shift {shift}: fundamental {f0}"
        );
        measured.push(envelope_centroid(&s) / before);
    }
    eprintln!("formant centroid ratios for -4, 0, +4 st: {measured:?}");
    let [down, none, up] = measured[..] else {
        unreachable!()
    };
    assert!((none - 1.0).abs() < 0.05, "{none}");
    assert!(up > 1.08, "{up}");
    assert!(down < 0.93, "{down}");
    // the factor param does the same as its semitones
    let mut by_factor = effect("formant", &[("factor", 2f32.powf(4.0 / 12.0))]);
    let mut by_semitones = effect("formant", &[("semitones", 4.0)]);
    assert_eq!(
        run(by_factor.as_mut(), &x[..24000], 128),
        run(by_semitones.as_mut(), &x[..24000], 128)
    );
}

// --- tune ---

fn tune(params: &[(&str, f32)], x: &[[f32; 2]]) -> Vec<[f32; 2]> {
    let mut e = effect("tune", params);
    run(e.as_mut(), x, 256)
}

#[test]
fn tune_at_speed_zero_pulls_450_hz_to_440_and_leaves_440_alone() {
    let a_chromatic = [("key", 9.0), ("scale", 5.0), ("speed", 0.0)];
    // 450 and 430 Hz start 39 cents off A4. signalsmith itself lands a transposed sine up to
    // about 5 cents off its target (1.3 Hz near 440, measured on the bare bridge), so the
    // pulled notes get 6 cents; 440 Hz needs no transposition and must stay put.
    for (input, within) in [(450.0, 6.0), (430.0, 6.0), (440.0, 0.5)] {
        let y = tune(&a_chromatic, &sine(input, 2.0));
        let got = steady(&y, 0.6).peak(300.0, 600.0);
        eprintln!("tune: {input} Hz in, FFT peak {got:.3} Hz out");
        assert!(cents(got, 440.0).abs() < within, "{input} -> {got}");
    }
    // A tuned 30 cents up: 440 Hz goes to 440 Hz + 30 cents
    let mut shifted = a_chromatic.to_vec();
    shifted.push(("reference", 30.0));
    let got = steady(&tune(&shifted, &sine(440.0, 2.0)), 0.6).peak(300.0, 600.0);
    eprintln!("tune: reference +30 cents, 440 Hz in, FFT peak {got:.3} Hz out");
    assert!((cents(got, 440.0) - 30.0).abs() < 6.0, "{got}");
}

#[test]
fn tune_with_mix_zero_is_the_input_delayed_by_its_latency() {
    let x = sine(450.0, 0.5);
    let mut e = effect("tune", &[("mix", 0.0)]);
    let d = e.latency();
    assert!(d > 0);
    let y = run(e.as_mut(), &x, 100);
    assert!(y[..d].iter().all(|f| *f == [0.0, 0.0]));
    assert_eq!(&y[d..], &x[..x.len() - d]);
}

fn is_c_major(note: i32) -> bool {
    [0, 2, 4, 5, 7, 9, 11].contains(&note.rem_euclid(12))
}

/// Share of frames within 15 cents of a C major note, and the notes they sat on.
fn snapped(frames: &[f64]) -> (f64, std::collections::BTreeSet<i32>) {
    let c_major = Scale::new(0, 0);
    let mut notes = std::collections::BTreeSet::new();
    let mut near = 0;
    for f in frames {
        let m = midi(*f as f32, 0.0);
        let n = c_major.nearest(m);
        if (m - n as f32).abs() < 0.15 {
            near += 1;
            notes.insert(n);
        }
    }
    (near as f64 / frames.len() as f64, notes)
}

#[test]
fn a_slow_sweep_is_snapped_to_the_notes_of_the_scale() {
    // A3 to A4 in six seconds: two semitones a second
    let x = tone(6.5, 0.5, |t| 220.0 * 2f64.powf(t.min(6.0) / 6.0));
    let y = tune(&[("key", 0.0), ("scale", 0.0), ("speed", 0.0)], &x);
    let latency = make("tune").map(|mut e| {
        e.reset(RATE);
        e.latency() as f32 / RATE
    });
    let start = 0.3 + latency.unwrap();
    let (input_share, _) = snapped(&track(&x, 0.3, 0.04));
    let (share, notes) = snapped(&track(&y, start, 0.04));
    eprintln!("sweep: {input_share:.2} of input frames on C major notes, {share:.2} of output; notes {notes:?}");
    assert!(input_share < 0.3, "{input_share}");
    assert!(share > 0.75, "{share}");
    // A3 B3 C4 D4 E4 F4 G4 all held on the way up
    for n in [57, 59, 60, 62, 64, 65, 67] {
        assert!(notes.contains(&n), "{n} missing from {notes:?}");
        assert!(is_c_major(n));
    }
}

#[test]
fn humanize_lets_a_sustained_note_drift_but_less_than_the_singer() {
    // 440 Hz wandering 30 cents either way at half a hertz
    let x = tone(6.0, 0.5, |t| {
        440.0 * 2f64.powf(30.0 * (PI * t).sin() / 1200.0)
    });
    let spread = |y: &[[f32; 2]], start: f32| {
        let frames = track(y, start, 0.03);
        let n = frames.len() as f64;
        (frames.iter().map(|f| cents(*f, 440.0).powi(2)).sum::<f64>() / n).sqrt()
    };
    let a = [("key", 9.0), ("scale", 5.0), ("speed", 0.0)];
    let strict = spread(&tune(&a, &x), 1.5);
    let human = spread(&tune(&[a[0], a[1], a[2], ("humanize", 1.0)], &x), 1.5);
    let sung = spread(&x, 1.5);
    eprintln!("humanize: sung {sung:.1}, strict {strict:.1}, humanized {human:.1} cents rms");
    assert!(strict < 8.0, "{strict}");
    assert!(human > strict + 4.0, "{human} vs {strict}");
    assert!(human < sung, "{human} vs {sung}");
}

// --- harmonizer, octaver, unison ---

#[test]
fn harmonizer_in_c_major_adds_the_diatonic_third_and_fifth() {
    let params = [
        ("key", 0.0),
        ("scale", 0.0),
        ("voices", 2.0),
        ("interval1", 2.0),
        ("interval2", 4.0),
        ("mix", 0.5),
    ];
    // on C: E (a major third) and G; on D: F (a minor third) and A
    for (root, third, fifth, wrong) in [
        (60.0, 64.0, 67.0, [63.0, 66.0]),
        (62.0, 65.0, 69.0, [66.0, 68.0]),
    ] {
        let mut e = effect("harmonizer", &params);
        let y = run(e.as_mut(), &sine(freq(root, 0.0) as f64, 2.0), 256);
        let s = steady(&y, 0.6);
        let level = |m: f32| s.level(freq(m, 0.0) as f64, 3.0);
        let lead = level(root);
        eprintln!(
            "harmonizer on {root}: lead {lead:.0}, third {:.0}, fifth {:.0}, wrong {:.1} {:.1}",
            level(third),
            level(fifth),
            level(wrong[0]),
            level(wrong[1])
        );
        assert!(level(third) > 0.3 * lead && level(fifth) > 0.3 * lead);
        for w in wrong {
            assert!(level(w) < 0.05 * lead, "{w}");
        }
    }
}

#[test]
fn octaver_layers_an_octave_down_and_up_at_their_levels() {
    let x = sine(220.0, 2.0);
    let mut both = effect("octaver", &[("down", 1.0), ("up", 1.0), ("dry", 1.0)]);
    let s = steady(&run(both.as_mut(), &x, 256), 0.6);
    let (low, mid, high) = (
        s.level(110.0, 2.0),
        s.level(220.0, 2.0),
        s.level(440.0, 2.0),
    );
    assert!(low > 0.3 * mid && high > 0.3 * mid, "{low} {mid} {high}");
    let mut down = effect("octaver", &[("down", 1.0), ("up", 0.0), ("dry", 0.0)]);
    let s = steady(&run(down.as_mut(), &x, 256), 0.6);
    let (low, mid, high) = (
        s.level(110.0, 2.0),
        s.level(220.0, 2.0),
        s.level(440.0, 2.0),
    );
    assert!(mid < 0.05 * low && high < 0.05 * low, "{low} {mid} {high}");
}

#[test]
fn unison_spreads_a_mono_voice_across_the_stereo_field() {
    let x = sine(440.0, 2.0);
    let side_share = |spread: f32| {
        let mut e = effect(
            "unison",
            &[
                ("voices", 8.0),
                ("detune", 20.0),
                ("spread", spread),
                ("mix", 1.0),
            ],
        );
        let y = run(e.as_mut(), &x, 256);
        let mid: Vec<f32> = y.iter().map(|f| 0.5 * (f[0] + f[1])).collect();
        let side: Vec<f32> = y.iter().map(|f| 0.5 * (f[0] - f[1])).collect();
        power(&side, 0.6) / power(&mid, 0.6)
    };
    let (none, half, full) = (side_share(0.0), side_share(0.5), side_share(1.0));
    eprintln!("unison side/mid power: spread 0 {none:.2e}, 0.5 {half:.3}, 1 {full:.3}");
    assert!(none < 1e-9, "{none}");
    assert!(full > 0.15, "{full}");
    assert!(none < half && half < full);
    // detuned voices: energy leaves the exact input frequency's neighbourhood
    // spread 1 pans the two voices hard left and right, so look at both channels
    let mut e = effect("unison", &[("voices", 2.0), ("detune", 50.0), ("mix", 1.0)]);
    let y = run(e.as_mut(), &x, 256);
    let both: Vec<f32> = y.iter().map(|f| f[0] + f[1]).collect();
    let s = Spectrum::of(&both[(0.6 * RATE) as usize..]);
    let (flat, sharp) = (freq(68.5, 0.0) as f64, freq(69.5, 0.0) as f64);
    assert!(s.level(flat, 2.0) > 3.0 * s.level(440.0, 1.0));
    assert!(s.level(sharp, 2.0) > 3.0 * s.level(440.0, 1.0));
}

// --- vibrato, drift, robot ---

#[test]
fn vibrato_swings_the_pitch_by_its_depth_at_its_rate() {
    let x = sine(440.0, 2.5);
    let mut e = effect("vibrato", &[("rate", 5.0), ("depth", 50.0)]);
    let frames = track(&run(e.as_mut(), &x, 256), 0.5, 0.01);
    let (lo, hi) = frames
        .iter()
        .fold((f64::MAX, f64::MIN), |(a, b), f| (a.min(*f), b.max(*f)));
    let want_hi = 440.0 * 2f64.powf(50.0 / 1200.0);
    let want_lo = 440.0 * 2f64.powf(-50.0 / 1200.0);
    assert!(
        (hi - want_hi).abs() < 2.5 && (lo - want_lo).abs() < 2.5,
        "{lo} {hi}"
    );
    // five swings a second over two seconds
    let ups = frames
        .windows(2)
        .filter(|w| w[0] < 440.0 && w[1] >= 440.0)
        .count();
    assert!((9..=11).contains(&ups), "{ups}");
    // no depth: the input, delayed by the reported latency
    let mut flat = effect("vibrato", &[("depth", 0.0)]);
    let d = flat.latency();
    assert!(d > 0);
    let y = run(flat.as_mut(), &x, 100);
    assert_eq!(&y[d..], &x[..x.len() - d]);
}

#[test]
fn drift_wanders_by_its_depth_and_repeats_with_its_seed() {
    let x = sine(440.0, 4.0);
    let render = |seed: f32, depth: f32| {
        let mut e = effect("drift", &[("seed", seed), ("depth", depth), ("rate", 1.0)]);
        run(e.as_mut(), &x, 256)
    };
    let a = render(7.0, 40.0);
    assert_eq!(a, render(7.0, 40.0));
    assert_ne!(a, render(8.0, 40.0));
    let wander: Vec<f64> = track(&a, 0.5, 0.05)
        .iter()
        .map(|f| cents(*f, 440.0).abs())
        .collect();
    let most = wander.iter().cloned().fold(0.0, f64::max);
    assert!(most > 10.0 && most < 45.0, "{most}");
    let still = steady(&render(7.0, 0.0), 0.5).peak(300.0, 600.0);
    assert!((still - 440.0).abs() < 0.5, "{still}");
}

#[test]
fn robot_holds_one_note_on_a_gliding_voice() {
    let x = tone(3.0, 0.5, |t| 200.0 + 100.0 * t / 3.0);
    let mut e = effect("robot", &[("note", 57.0)]);
    let start = 0.3 + e.latency() as f32 / RATE;
    let y = run(e.as_mut(), &x, 256);
    let frames = track(&y, start, 0.04);
    let held = frames
        .iter()
        .filter(|f| cents(**f, 220.0).abs() < 20.0)
        .count() as f64
        / frames.len() as f64;
    let input = track(&x, 0.3, 0.04);
    let input_held = input
        .iter()
        .filter(|f| cents(**f, 220.0).abs() < 20.0)
        .count() as f64
        / input.len() as f64;
    eprintln!("robot: {held:.2} of output frames on A3, {input_held:.2} of input");
    assert!(held > 0.85, "{held}");
    assert!(input_held < 0.2, "{input_held}");
}

#[test]
fn a_pitch_rack_from_json_shifts_by_name() {
    let mut rack = Rack::from_json(
        r#"{"effects": [{"effect": "pitch", "params": {"semitones": 7, "preserve": "on"}}]}"#,
    )
    .unwrap();
    rack.reset(RATE).unwrap();
    assert!(rack.latency() > 0);
    let mut y = sine(440.0, 1.5);
    for chunk in y.chunks_mut(512) {
        rack.process(chunk);
    }
    let got = steady(&y, 0.5).peak(300.0, 900.0);
    let want = 440.0 * 2f64.powf(7.0 / 12.0);
    assert!((got / want - 1.0).abs() < 0.01, "{got}");
}

// --- behaviour the host's block size and live changes must not alter ---

#[test]
fn drift_does_not_depend_on_the_block_size() {
    let x = sine(440.0, 2.0);
    let render = |block: usize| {
        let mut e = effect("drift", &[("seed", 3.0), ("depth", 60.0), ("rate", 2.0)]);
        run(e.as_mut(), &x, block)
    };
    let want = render(512);
    for block in [1, 64, 100] {
        let got = render(block);
        let first = got.iter().zip(&want).position(|(a, b)| a != b);
        assert!(
            first.is_none(),
            "block {block} differs from frame {first:?}"
        );
    }
}

fn rms(y: &[[f32; 2]]) -> f64 {
    let sum: f64 = y
        .iter()
        .map(|f| (f[0] as f64).powi(2) + (f[1] as f64).powi(2))
        .sum();
    (sum / (2 * y.len()) as f64).sqrt()
}

#[test]
fn adding_voices_keeps_the_sounding_ones_going() {
    let x = sine(freq(60.0, 0.0) as f64, 3.0);
    let split = (1.5 * RATE) as usize;
    let harmonizer: &[(&str, f32)] = &[("key", 0.0), ("scale", 0.0), ("mix", 1.0), ("voices", 1.0)];
    // no detune: the voices sound in unison, so the level does not beat
    let unison: &[(&str, f32)] = &[
        ("detune", 0.0),
        ("spread", 0.0),
        ("mix", 1.0),
        ("voices", 2.0),
    ];
    for (name, params, more) in [("harmonizer", harmonizer, 2.0), ("unison", unison, 4.0)] {
        let mut e = effect(name, params);
        let mut y = x.clone();
        for chunk in y[..split].chunks_mut(256) {
            e.process(chunk);
        }
        e.set("voices", more).unwrap();
        for chunk in y[split..].chunks_mut(256) {
            e.process(chunk);
        }
        let before = rms(&y[split - 24000..split]);
        let quietest = y[split..split + 24000]
            .chunks(960)
            .map(rms)
            .fold(f64::MAX, f64::min);
        eprintln!("{name}: {before:.3} before more voices, quietest 20 ms after {quietest:.3}");
        assert!(quietest > 0.5 * before, "{name}: {quietest} vs {before}");
    }
}

#[test]
fn after_a_breath_tune_starts_the_next_note_without_the_old_correction() {
    // 430 Hz, 39 cents flat of A4; a breath of silence; 450 Hz, 39 cents sharp
    let mut x = sine(430.0, 1.0);
    x.extend(vec![[0.0f32; 2]; (0.4 * RATE) as usize]);
    x.extend(sine(450.0, 1.0));
    // a slow retune, so a correction carried over would last long enough to measure
    let mut e = effect("tune", &[("key", 9.0), ("scale", 5.0), ("speed", 200.0)]);
    let onset = 1.4 + e.latency() as f32 / RATE;
    let y = run(e.as_mut(), &x, 256);
    let start = track(&y, onset + 0.02, 0.02);
    let start: Vec<f64> = start[..4].iter().map(|f| cents(*f, 450.0)).collect();
    eprintln!("tune after a breath: the first 80 ms in cents from 450 Hz {start:?}");
    // never sharper than the sung note, beyond signalsmith's own few cents
    assert!(start.iter().all(|c| *c < 8.0), "{start:?}");
    // and the flat note before it was corrected
    let held = steady(&y[..(0.95 * RATE) as usize + e.latency()], 0.6).peak(300.0, 600.0);
    assert!(cents(held, 440.0).abs() < 8.0, "{held}");
}

#[test]
fn harmonizer_voices_add_up_by_power() {
    let x = sine(freq(60.0, 0.0) as f64, 2.0);
    let peak = |params: &[(&str, f32)]| {
        let mut all = vec![("key", 0.0), ("scale", 0.0), ("mix", 1.0)];
        all.extend_from_slice(params);
        let y = run(effect("harmonizer", &all).as_mut(), &x, 256);
        y[(0.6 * RATE) as usize..]
            .iter()
            .map(|f| f[0].abs())
            .fold(0.0, f32::max)
    };
    // each of the four default intervals sounding alone, as the first voice
    let alone: Vec<f32> = [2.0, 4.0, -3.0, 7.0]
        .iter()
        .map(|d| peak(&[("voices", 1.0), ("interval1", *d)]))
        .collect();
    let four = peak(&[("voices", 4.0)]);
    let loudest = alone.iter().cloned().fold(0.0, f32::max);
    eprintln!("harmonizer peak: voices alone {alone:?}, all four {four:.3}");
    // four voices, each scaled by 1/sqrt(4), cannot pass twice the loudest of them
    assert!(four <= 2.0 * loudest * 1.02, "{four} vs {loudest}");
    assert!(four > loudest, "{four} vs {loudest}");
}
