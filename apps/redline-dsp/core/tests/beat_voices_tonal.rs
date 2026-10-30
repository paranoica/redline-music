//! The melodic voices (fm, pluck, saw) and the sampler: pitch, timbre, envelopes, purity,
//! the sample bank and no allocation while playing. Every input is generated here.

use redline_dsp_core::beat::events::Event;
use redline_dsp_core::beat::instruments::instrument;
use redline_dsp_core::beat::voices::{hz, make, Ctx, SampleBank, Voice};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::f64::consts::TAU;

struct Counting;

thread_local! {
    // per thread, so tests running side by side do not count each other
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}

fn count() {
    let _ = ALLOCATIONS.try_with(|c| c.set(c.get() + 1));
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        count();
        unsafe { System.realloc(ptr, layout, new_size) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        count();
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

/// Calls into the allocator while f runs, frees included.
fn allocations(f: impl FnOnce()) -> usize {
    let before = ALLOCATIONS.with(Cell::get);
    f();
    ALLOCATIONS.with(Cell::get) - before
}

const RATE: f32 = 48000.0;

fn frames(seconds: f64) -> u64 {
    (seconds * RATE as f64).round() as u64
}

fn note_event(id: u32, note: f32, vel: f32, off_s: f64) -> Event {
    Event {
        id,
        sound: 0,
        start: 1000,
        off: Some(1000 + frames(off_s)),
        end: None,
        note,
        vel,
        decay: 1.0,
        roll: 0,
        legato_from: None,
        section: 0,
        bar: 0,
        step: 0,
    }
}

/// The instrument's defaults with the named ones replaced.
fn params(name: &str, set: &[(&str, f32)]) -> Vec<f32> {
    let inst = instrument(name).unwrap();
    inst.params
        .iter()
        .map(|p| {
            set.iter()
                .find(|(n, _)| *n == p.name)
                .map_or(p.default, |(_, v)| *v)
        })
        .collect()
}

fn voice(name: &str) -> Box<dyn Voice> {
    make(name, RATE).unwrap_or_else(|| panic!("no voice for {name}"))
}

/// `n` frames of the voice after starting it, rendered `block` frames at a time.
fn play(
    v: &mut dyn Voice,
    e: &Event,
    p: &[f32],
    bank: &SampleBank,
    n: usize,
    block: usize,
) -> Vec<[f32; 2]> {
    let ctx = Ctx { rate: RATE, bank };
    v.start(e, p, &ctx);
    let mut out = vec![[0.0f32; 2]; n];
    for chunk in out.chunks_mut(block) {
        v.render(chunk, &ctx);
    }
    out
}

fn render(name: &str, e: &Event, p: &[f32], bank: &SampleBank, seconds: f64) -> Vec<[f32; 2]> {
    play(
        voice(name).as_mut(),
        e,
        p,
        bank,
        frames(seconds) as usize,
        4096,
    )
}

fn left(x: &[[f32; 2]]) -> Vec<f64> {
    x.iter().map(|f| f[0] as f64).collect()
}

fn span(x: &[f64], from_s: f64, to_s: f64) -> &[f64] {
    &x[frames(from_s) as usize..frames(to_s) as usize]
}

fn rms(x: &[f64]) -> f64 {
    (x.iter().map(|v| v * v).sum::<f64>() / x.len() as f64).sqrt()
}

/// Power at `f` Hz of a Hann-windowed signal (Goertzel).
fn power(x: &[f64], f: f64) -> f64 {
    let n = x.len() as f64;
    let c = 2.0 * (TAU * f / RATE as f64).cos();
    let (mut s1, mut s2) = (0.0, 0.0);
    for (i, v) in x.iter().enumerate() {
        let w = 0.5 - 0.5 * (TAU * i as f64 / n).cos();
        let s = v * w + c * s1 - s2;
        s2 = s1;
        s1 = s;
    }
    s1 * s1 + s2 * s2 - c * s1 * s2
}

/// The strongest frequency between `lo` and `hi`, scanned at `step` Hz.
fn peak(x: &[f64], lo: f64, hi: f64, step: f64) -> f64 {
    let mut best = (lo, -1.0);
    let mut f = lo;
    while f <= hi {
        let p = power(x, f);
        if p > best.1 {
            best = (f, p);
        }
        f += step;
    }
    best.0
}

/// The peak near `expect`, found to a 0.01% grid.
fn pitch(x: &[f64], expect: f64) -> f64 {
    let coarse = peak(x, expect * 0.9, expect * 1.1, expect * 0.002);
    peak(x, coarse * 0.997, coarse * 1.003, expect * 0.0001)
}

fn assert_near(got: f64, want: f64, tolerance: f64) {
    let err = (got - want).abs() / want;
    assert!(
        err < tolerance,
        "{got:.3} Hz is {:.3}% off {want:.3} Hz",
        err * 100.0
    );
}

/// A sine at `f` Hz, `seconds` long at `rate`, as i16 at half scale.
fn sine(f: f64, rate: f64, seconds: f64) -> Vec<i16> {
    (0..(seconds * rate) as usize)
        .map(|i| ((TAU * f * i as f64 / rate).sin() * 16384.0).round() as i16)
        .collect()
}

fn bank_with_sine(root: u8, f: f64) -> SampleBank {
    let mut bank = SampleBank::default();
    bank.load(
        "strings",
        root,
        0..=127,
        RATE,
        &sine(f, RATE as f64, 2.0),
        1,
    )
    .unwrap();
    bank
}

fn empty() -> SampleBank {
    SampleBank::default()
}

// --- fm ---

#[test]
fn fm_keys_sounds_at_the_note() {
    let p = params("fm", &[("preset", 1.0)]);
    let x = left(&render(
        "fm",
        &note_event(0, 69.0, 1.0, 1.0),
        &p,
        &empty(),
        1.0,
    ));
    // once the tine's modulation has died, the carrier at A4 is all there is
    let tail = span(&x, 0.3, 1.0);
    assert_near(peak(tail, 50.0, 4000.0, 2.0), 440.0, 0.01);
    assert_near(pitch(tail, 440.0), 440.0, 0.001);
    // and it holds while the key is down
    assert!(rms(span(&x, 0.9, 1.0)) > 0.5 * rms(span(&x, 0.3, 0.4)));
}

#[test]
fn fm_bell_has_inharmonic_partials() {
    let f = 440.0;
    let bell = |ratio: f32| {
        let p = params(
            "fm",
            &[
                ("preset", 0.0),
                ("ratio", ratio),
                ("index", 3.0),
                ("decay", 2.0),
            ],
        );
        left(&render(
            "fm",
            &note_event(0, 69.0, 1.0, 1.0),
            &p,
            &empty(),
            0.5,
        ))
    };
    let x = bell(1.4);
    let head = span(&x, 0.0, 0.4);
    let fundamental = power(head, f);
    // carrier plus one modulator step: 1 + 1.4 = 2.4 times the note
    assert!(power(head, 2.4 * f) > 0.1 * fundamental);
    for k in [2.0, 3.0] {
        assert!(power(head, k * f) < 0.01 * fundamental, "harmonic {k}");
    }
    // a whole-number ratio puts nothing there
    let y = bell(2.0);
    let head = span(&y, 0.0, 0.4);
    assert!(power(head, 2.4 * f) < 0.001 * power(head, 3.0 * f));
    // and the bell dies away though the note is held
    assert!(rms(span(&x, 0.45, 0.5)) < 0.7 * rms(span(&x, 0.0, 0.05)));
}

// --- pluck ---

#[test]
fn pluck_is_in_tune_low_and_high() {
    for (note, from, to) in [(57.0, 0.05, 0.85), (96.0, 0.01, 0.25), (40.0, 0.05, 0.85)] {
        let x = left(&render(
            "pluck",
            &note_event(3, note, 1.0, 1.0),
            &params("pluck", &[]),
            &empty(),
            1.0,
        ));
        let want = hz(note as f64);
        assert_near(pitch(span(&x, from, to), want), want, 0.001);
    }
}

#[test]
fn pluck_stays_in_tune_whatever_the_tone() {
    for tone in [0.0, 1.0] {
        let p = params("pluck", &[("tone", tone)]);
        let x = left(&render(
            "pluck",
            &note_event(3, 96.0, 1.0, 1.0),
            &p,
            &empty(),
            0.3,
        ));
        assert_near(pitch(span(&x, 0.01, 0.2), hz(96.0)), hz(96.0), 0.001);
    }
}

#[test]
fn pluck_decays_by_its_decay() {
    let play = |decay: f32| {
        let p = params("pluck", &[("decay", decay)]);
        left(&render(
            "pluck",
            &note_event(3, 57.0, 1.0, 2.0),
            &p,
            &empty(),
            1.1,
        ))
    };
    // the fundamental falls 60 dB in `decay` seconds: 10^(-3 / decay) over one second
    let fundamental = |x: &[f64], at: f64| power(span(x, at, at + 0.1), 220.0).sqrt();
    for decay in [0.5, 2.0, 8.0] {
        let x = play(decay);
        let got = fundamental(&x, 1.0) / fundamental(&x, 0.0);
        let want = 10f64.powf(-3.0 / decay as f64);
        assert!(
            (got / want).log10().abs() < 0.1,
            "decay {decay}: fell to {got:.3e}, want {want:.3e}"
        );
    }
}

#[test]
fn pluck_plays_midi_0_and_127() {
    for note in [0.0, 127.0] {
        let x = render(
            "pluck",
            &note_event(1, note, 1.0, 0.5),
            &params("pluck", &[]),
            &empty(),
            1.0,
        );
        assert!(x.iter().flatten().all(|v| v.is_finite() && v.abs() < 4.0));
        assert!(x.iter().any(|f| f[0] != 0.0), "note {note} is silent");
    }
}

// --- saw ---

fn saw_at(note: f32, set: &[(&str, f32)], seconds: f64) -> Vec<[f32; 2]> {
    render(
        "saw",
        &note_event(9, note, 1.0, seconds),
        &params("saw", set),
        &empty(),
        seconds,
    )
}

fn saw(set: &[(&str, f32)], seconds: f64) -> Vec<[f32; 2]> {
    saw_at(45.0, set, seconds)
}

#[test]
fn saw_fundamental_is_the_note() {
    let x = left(&saw(
        &[("voices", 1.0), ("detune", 0.0), ("cutoff", 16000.0)],
        1.0,
    ));
    let tail = span(&x, 0.1, 1.0);
    assert_near(peak(tail, 50.0, 400.0, 1.0), 110.0, 0.01);
    assert_near(pitch(tail, 110.0), 110.0, 0.001);
}

#[test]
fn saw_voices_thicken_and_spread() {
    let one = saw(&[("voices", 1.0)], 0.3);
    let five = saw(&[("voices", 5.0)], 0.3);
    assert_ne!(one, five);
    // one saw sits in the middle, five are spread across
    assert!(one.iter().all(|f| f[0] == f[1]));
    assert!(five.iter().any(|f| (f[0] - f[1]).abs() > 1e-3));
}

#[test]
fn saw_cutoff_takes_the_highs() {
    let highs = |cutoff: f32| {
        let x = left(&saw(
            &[("voices", 1.0), ("cutoff", cutoff), ("resonance", 0.1)],
            0.5,
        ));
        let tail = span(&x, 0.1, 0.5);
        (19..=90)
            .map(|k| power(tail, 110.0 * k as f64))
            .sum::<f64>()
    };
    let dark = highs(200.0);
    let open = highs(16000.0);
    assert!(open > 0.0);
    assert!(dark < 1e-3 * open, "{dark} against {open}");
}

#[test]
fn saw_keeps_aliases_down() {
    // a high note whose partials past Nyquist fold back between its harmonics; a naive saw
    // puts them about 17 dB under the fundamental all together
    let f = hz(90.0);
    let x = left(&saw_at(
        90.0,
        &[("voices", 1.0), ("cutoff", 16000.0), ("resonance", 0.0)],
        0.5,
    ));
    let tail = span(&x, 0.1, 0.5);
    let nyquist = RATE as f64;
    let aliases: f64 = (17..80)
        .map(|k| {
            let h = (k as f64 * f) % nyquist;
            h.min(nyquist - h)
        })
        .filter(|a| (100.0..12000.0).contains(a))
        .map(|a| power(tail, a))
        .sum();
    assert!(aliases < 1e-3 * power(tail, f), "{aliases:e}");
}

// --- sampler ---

#[test]
fn sampler_pitches_the_zone_by_the_note() {
    let bank = bank_with_sine(60, hz(60.0));
    let p = params("sampler", &[]);
    for (note, tune) in [(72.0, 0.0), (60.0, 0.0), (60.0, 12.0), (55.0, 0.0)] {
        let p = params("sampler", &[("tune", tune)]);
        let x = left(&render(
            "sampler",
            &note_event(0, note, 1.0, 1.0),
            &p,
            &bank,
            0.5,
        ));
        let want = hz(note as f64 + tune as f64);
        assert_near(pitch(span(&x, 0.05, 0.45), want), want, 0.001);
    }
    // a zone recorded at half the rate plays at its own pitch all the same
    let mut half = SampleBank::default();
    half.load(
        "strings",
        60,
        0..=127,
        24000.0,
        &sine(hz(60.0), 24000.0, 2.0),
        1,
    )
    .unwrap();
    let x = left(&render(
        "sampler",
        &note_event(0, 60.0, 1.0, 1.0),
        &p,
        &half,
        0.5,
    ));
    assert_near(pitch(span(&x, 0.05, 0.45), hz(60.0)), hz(60.0), 0.001);
}

#[test]
fn sampler_picks_the_velocity_layer() {
    let mut bank = SampleBank::default();
    bank.load(
        "strings",
        60,
        0..=63,
        RATE,
        &sine(300.0, RATE as f64, 1.0),
        1,
    )
    .unwrap();
    bank.load(
        "strings",
        60,
        64..=127,
        RATE,
        &sine(600.0, RATE as f64, 1.0),
        1,
    )
    .unwrap();
    let p = params("sampler", &[]);
    // velocities on both sides of the split
    for (vel, want) in [(0.0, 300.0), (63.0, 300.0), (64.0, 600.0), (127.0, 600.0)] {
        let e = note_event(0, 60.0, vel / 127.0, 1.0);
        let x = left(&render("sampler", &e, &p, &bank, 0.3));
        if vel == 0.0 {
            assert!(x.iter().all(|v| *v == 0.0));
            continue;
        }
        let s = span(&x, 0.02, 0.28);
        assert!(power(s, want) > 100.0 * power(s, 900.0 - want), "vel {vel}");
    }
}

#[test]
fn sampler_picks_the_nearest_root() {
    let mut bank = SampleBank::default();
    bank.load(
        "strings",
        48,
        0..=127,
        RATE,
        &sine(100.0, RATE as f64, 1.0),
        1,
    )
    .unwrap();
    bank.load(
        "strings",
        72,
        0..=127,
        RATE,
        &sine(1000.0, RATE as f64, 1.0),
        1,
    )
    .unwrap();
    let p = params("sampler", &[]);
    let x = left(&render(
        "sampler",
        &note_event(0, 70.0, 1.0, 1.0),
        &p,
        &bank,
        0.3,
    ));
    let want = 1000.0 * (-2.0f64 / 12.0).exp2();
    assert_near(pitch(span(&x, 0.02, 0.28), want), want, 0.001);
    let x = left(&render(
        "sampler",
        &note_event(0, 50.0, 1.0, 1.0),
        &p,
        &bank,
        0.3,
    ));
    let want = 100.0 * (2.0f64 / 12.0).exp2();
    assert_near(pitch(span(&x, 0.02, 0.28), want), want, 0.001);
}

#[test]
fn sampler_plays_stereo_zones_per_side() {
    let (l, r) = (sine(300.0, RATE as f64, 0.5), sine(700.0, RATE as f64, 0.5));
    let both: Vec<i16> = l.iter().zip(&r).flat_map(|(a, b)| [*a, *b]).collect();
    let mut bank = SampleBank::default();
    bank.load("strings", 60, 0..=127, RATE, &both, 2).unwrap();
    let x = render(
        "sampler",
        &note_event(0, 60.0, 1.0, 1.0),
        &params("sampler", &[]),
        &bank,
        0.3,
    );
    let (xl, xr) = (left(&x), x.iter().map(|f| f[1] as f64).collect::<Vec<_>>());
    assert_near(pitch(span(&xl, 0.02, 0.28), 300.0), 300.0, 0.001);
    assert_near(pitch(span(&xr, 0.02, 0.28), 700.0), 700.0, 0.001);
}

#[test]
fn sample_bank_refuses_past_its_budget() {
    assert_eq!(SampleBank::default().budget(), 48 * 1024 * 1024);
    let mut bank = SampleBank::with_budget(1000);
    bank.load("a", 60, 0..=127, RATE, &[1; 600], 1).unwrap();
    let err = bank.load("a", 62, 0..=127, RATE, &[1; 402], 2).unwrap_err();
    for part in ["402", "600", "1000"] {
        assert!(err.contains(part), "{err}");
    }
    assert_eq!(bank.used(), 600);
    // exactly up to the budget is fine
    bank.load("b", 60, 0..=127, RATE, &[1; 400], 2).unwrap();
    assert_eq!(bank.used(), 1000);
    assert!(bank.load("b", 60, 0..=127, RATE, &[1; 1], 1).is_err());
    bank.clear();
    assert_eq!(bank.used(), 0);
    assert_eq!(bank.library("a"), None);
    bank.load("a", 60, 0..=127, RATE, &[1; 1000], 1).unwrap();
}

#[test]
// a reversed velocity range is one of the bad zones
#[allow(clippy::reversed_empty_ranges)]
fn sample_bank_refuses_bad_zones() {
    let mut bank = SampleBank::default();
    let bad = [
        bank.load("", 60, 0..=127, RATE, &[1; 4], 1),
        bank.load("a", 128, 0..=127, RATE, &[1; 4], 1),
        bank.load("a", 60, 70..=60, RATE, &[1; 4], 1),
        bank.load("a", 60, 0..=128, RATE, &[1; 4], 1),
        bank.load("a", 60, 0..=127, 0.0, &[1; 4], 1),
        bank.load("a", 60, 0..=127, f32::NAN, &[1; 4], 1),
        bank.load("a", 60, 0..=127, RATE, &[1; 6], 3),
        bank.load("a", 60, 0..=127, RATE, &[1; 5], 2),
        bank.load("a", 60, 0..=127, RATE, &[], 1),
    ];
    for (i, r) in bad.iter().enumerate() {
        assert!(r.is_err(), "case {i} was taken");
    }
    assert_eq!(bank.used(), 0);
}

#[test]
fn sampler_finds_its_library_by_index() {
    let mut bank = SampleBank::default();
    bank.load("low", 60, 0..=127, RATE, &sine(200.0, RATE as f64, 1.0), 1)
        .unwrap();
    bank.load("high", 60, 0..=127, RATE, &sine(800.0, RATE as f64, 1.0), 1)
        .unwrap();
    assert_eq!(bank.library("low"), Some(0));
    assert_eq!(bank.library("high"), Some(1));
    assert_eq!(bank.library("none"), None);
    let p = params("sampler", &[]);
    let e = note_event(0, 60.0, 1.0, 1.0);
    let n = frames(0.3) as usize;
    for (library, want) in [(0, 200.0), (1, 800.0)] {
        let x = left(&play(
            SampleBank::voice(RATE, library).as_mut(),
            &e,
            &p,
            &bank,
            n,
            512,
        ));
        assert_near(pitch(span(&x, 0.02, 0.28), want), want, 0.001);
    }
    // make() plays the first library
    let x = left(&render("sampler", &e, &p, &bank, 0.3));
    assert_near(pitch(span(&x, 0.02, 0.28), 200.0), 200.0, 0.001);
    // an index past the libraries is silent
    let x = play(SampleBank::voice(RATE, 2).as_mut(), &e, &p, &bank, n, 512);
    assert!(x.iter().flatten().all(|v| *v == 0.0));
}

#[test]
fn sampler_without_samples_is_silent() {
    let e = note_event(0, 60.0, 1.0, 1.0);
    let p = params("sampler", &[]);
    let mut v = voice("sampler");
    let bank = empty();
    let ctx = Ctx {
        rate: RATE,
        bank: &bank,
    };
    v.start(&e, &p, &ctx);
    let mut out = vec![[0.0f32; 2]; 4800];
    assert!(!v.render(&mut out, &ctx));
    assert!(out.iter().flatten().all(|v| *v == 0.0));

    // the bank emptied after the start: silence, not a panic
    let full = bank_with_sine(60, 261.0);
    v.start(
        &e,
        &p,
        &Ctx {
            rate: RATE,
            bank: &full,
        },
    );
    assert!(!v.render(&mut out, &ctx));
    assert!(out.iter().flatten().all(|v| *v == 0.0));
}

#[test]
fn sampler_stops_at_the_zone_end() {
    let mut bank = SampleBank::default();
    bank.load(
        "strings",
        60,
        0..=127,
        RATE,
        &sine(300.0, RATE as f64, 0.1),
        1,
    )
    .unwrap();
    let x = left(&render(
        "sampler",
        &note_event(0, 72.0, 1.0, 1.0),
        &params("sampler", &[]),
        &bank,
        0.3,
    ));
    // a 0.1 s zone an octave up lasts 0.05 s
    assert!(rms(span(&x, 0.01, 0.04)) > 0.1);
    assert!(span(&x, 0.051, 0.3).iter().all(|v| *v == 0.0));
}

// --- all tonal voices ---

/// Each voice with params and a bank that make it sound.
fn cases() -> Vec<(&'static str, Vec<f32>, SampleBank)> {
    vec![
        ("fm", params("fm", &[("preset", 1.0)]), empty()),
        ("fm", params("fm", &[("preset", 2.0)]), empty()),
        ("pluck", params("pluck", &[]), empty()),
        ("saw", params("saw", &[("resonance", 0.7)]), empty()),
        ("sampler", params("sampler", &[]), bank_with_sine(60, 261.0)),
    ]
}

#[test]
fn block_size_does_not_change_the_output() {
    let mut e = note_event(17, 64.0, 0.8, 0.2);
    e.end = Some(e.start + frames(0.35));
    let n = frames(0.5) as usize;
    for (name, p, bank) in cases() {
        let whole = play(voice(name).as_mut(), &e, &p, &bank, n, n);
        assert!(whole.iter().any(|f| f[0] != 0.0), "{name} is silent");
        for block in [1, 7, 128] {
            let x = play(voice(name).as_mut(), &e, &p, &bank, n, block);
            assert!(x == whole, "{name} in blocks of {block}");
        }
    }
}

#[test]
fn a_reused_voice_plays_like_a_fresh_one() {
    let first = note_event(4, 40.0, 1.0, 0.1);
    let second = note_event(5, 76.0, 0.6, 0.2);
    let n = frames(0.4) as usize;
    for (name, p, bank) in cases() {
        let mut v = voice(name);
        // the first note cut off half way through
        play(v.as_mut(), &first, &p, &bank, n / 2, 128);
        let reused = play(v.as_mut(), &second, &p, &bank, n, 128);
        let fresh = play(voice(name).as_mut(), &second, &p, &bank, n, 128);
        assert!(reused == fresh, "{name}");
    }
}

#[test]
fn a_cut_voice_is_silent_5_ms_after_its_end() {
    let mut e = note_event(2, 60.0, 1.0, 1.0);
    let end = frames(0.1);
    e.end = Some(e.start + end);
    let fade = frames(0.005);
    for (name, p, bank) in cases() {
        let mut v = voice(name);
        let ctx = Ctx {
            rate: RATE,
            bank: &bank,
        };
        v.start(&e, &p, &ctx);
        // one frame short of the fade's end the voice is still going
        let mut out = vec![[0.0f32; 2]; (end + fade - 1) as usize];
        assert!(v.render(&mut out, &ctx), "{name}");
        // still sounding up to the cut: the fade is not a jump
        assert!(
            out[end as usize - 10..end as usize]
                .iter()
                .any(|f| f[0] != 0.0),
            "{name}"
        );
        let mut after = vec![[0.0f32; 2]; 4800];
        assert!(!v.render(&mut after, &ctx), "{name} rings on");
        // the fade's last frame, then nothing from 5 ms after the cut
        assert!(after[0][0].abs() < 0.01, "{name}");
        assert!(after[1..].iter().flatten().all(|v| *v == 0.0), "{name}");
    }
}

#[test]
fn a_released_voice_falls_silent_after_its_release() {
    let e = note_event(2, 60.0, 1.0, 0.1);
    for (name, p, bank) in cases() {
        let release = match name {
            "fm" => p[5],
            "pluck" => p[2],
            "saw" => p[5],
            _ => p[2],
        } as f64;
        let x = left(&render(name, &e, &p, &bank, 0.1 + release + 0.1));
        assert!(
            rms(span(&x, 0.05, 0.1)) > 0.01,
            "{name} is quiet while held"
        );
        // it fades: the last few frames before silence are close to nothing
        let held = span(&x, 0.05, 0.1)
            .iter()
            .fold(0.0f64, |m, v| m.max(v.abs()));
        let off = frames(0.1) as usize;
        let tail = (frames(release) as usize / 50).max(2);
        let last = off + frames(release) as usize;
        let edge = x[last - tail..=last]
            .iter()
            .fold(0.0f64, |m, v| m.max(v.abs()));
        assert!(
            edge < 0.05 * held,
            "{name} stops with a click: {edge} against {held}"
        );
        let gone = frames(0.1 + release) as usize + 1;
        assert!(x[gone..].iter().all(|v| *v == 0.0), "{name} rings on");
    }
}

#[test]
fn velocity_scales_the_level() {
    for (name, p, bank) in cases() {
        let x = |vel: f32| {
            left(&render(
                name,
                &note_event(6, 62.0, vel, 0.2),
                &p,
                &bank,
                0.2,
            ))
        };
        let ratio = rms(&x(0.5)) / rms(&x(1.0));
        assert!((ratio - 0.5).abs() < 1e-3, "{name}: {ratio}");
        assert!(x(0.0).iter().all(|v| *v == 0.0), "{name}");
    }
}

#[test]
fn extreme_params_stay_finite_and_bounded() {
    let mut tries = Vec::new();
    for (name, _, bank) in cases() {
        let inst = instrument(name).unwrap();
        let lows: Vec<f32> = inst.params.iter().map(|p| p.low).collect();
        let highs: Vec<f32> = inst.params.iter().map(|p| p.high).collect();
        let wild = vec![f32::NAN; inst.params.len()];
        for p in [lows, highs, wild, Vec::new()] {
            for note in [0.0, 60.0, 127.0] {
                tries.push((name, p.clone(), note, bank_copy(&bank)));
            }
        }
    }
    for (name, p, note, bank) in tries {
        let x = render(name, &note_event(8, note, 1.0, 0.3), &p, &bank, 0.5);
        assert!(
            x.iter().flatten().all(|v| v.is_finite() && v.abs() < 10.0),
            "{name} at {note} with {p:?}"
        );
    }
}

fn bank_copy(bank: &SampleBank) -> SampleBank {
    if bank.used() == 0 {
        empty()
    } else {
        bank_with_sine(60, 261.0)
    }
}

#[test]
fn start_and_render_allocate_nothing() {
    for (name, p, bank) in cases() {
        let mut v = voice(name);
        let ctx = Ctx {
            rate: RATE,
            bank: &bank,
        };
        let mut out = vec![[0.0f32; 2]; 128];
        for note in [0.0, 60.0, 127.0] {
            let e = note_event(1, note, 1.0, 0.1);
            let n = allocations(|| {
                v.start(&e, &p, &ctx);
                for _ in 0..80 {
                    v.render(&mut out, &ctx);
                }
            });
            assert_eq!(n, 0, "{name} at {note}");
        }
    }
}
