use redline_dsp_core::beat::events::Event;
use redline_dsp_core::beat::instruments::instrument;
use redline_dsp_core::beat::voices::{make, Ctx, SampleBank};

const RATE: f32 = 48000.0;
const DRUMS: &[&str] = &[
    "kick",
    "snare",
    "clap",
    "hat_closed",
    "hat_open",
    "rim",
    "cowbell",
];
const ALL: &[&str] = &[
    "kick",
    "bass808",
    "snare",
    "clap",
    "hat_closed",
    "hat_open",
    "rim",
    "cowbell",
];
/// The fade a cut voice takes, in frames.
const FADE: usize = (0.005 * RATE as f64) as usize;

fn frames(s: f64) -> usize {
    (s * RATE as f64).round() as usize
}

fn defaults(name: &str) -> Vec<f32> {
    instrument(name)
        .unwrap()
        .params
        .iter()
        .map(|p| p.default)
        .collect()
}

fn with(name: &str, set: &[(&str, f32)]) -> Vec<f32> {
    let specs = instrument(name).unwrap().params;
    let mut p = defaults(name);
    for (key, v) in set {
        let i = specs
            .iter()
            .position(|s| s.name == *key)
            .unwrap_or_else(|| panic!("{name} has no param {key}"));
        p[i] = *v;
    }
    p
}

fn extreme(name: &str, high: bool) -> Vec<f32> {
    instrument(name)
        .unwrap()
        .params
        .iter()
        .map(|p| if high { p.high } else { p.low })
        .collect()
}

fn is_note(name: &str) -> bool {
    name == "bass808"
}

/// A hit at frame 0; a note instrument's A1 is held for 0.3 s.
fn hit(name: &str, id: u32) -> Event {
    Event {
        id,
        sound: 0,
        start: 0,
        off: is_note(name).then(|| frames(0.3) as u64),
        end: None,
        note: if is_note(name) { 33.0 } else { 60.0 },
        vel: 1.0,
        decay: 1.0,
        roll: 0,
        legato_from: None,
        section: 0,
        bar: 0,
        step: 0,
    }
}

/// Frames to the end of the instrument's longest life for the event.
fn life(name: &str, e: &Event) -> usize {
    let inst = instrument(name).unwrap();
    match e.off {
        Some(off) => off as usize + frames(inst.max_life_s as f64),
        None => frames(inst.max_life_s as f64 * e.decay as f64),
    }
}

struct Run {
    out: Vec<[f32; 2]>,
    /// The first frame the voice reported silent at, if it did.
    silent_at: Option<usize>,
}

fn run_on(
    voice: &mut dyn redline_dsp_core::beat::voices::Voice,
    p: &[f32],
    e: &Event,
    n: usize,
    block: usize,
) -> Run {
    let bank = SampleBank::default();
    let ctx = Ctx {
        rate: RATE,
        bank: &bank,
    };
    voice.start(e, p, &ctx);
    let mut out = vec![[0.0f32; 2]; n];
    let mut silent_at = None;
    let mut at = 0;
    while at < n {
        let m = block.min(n - at);
        if !voice.render(&mut out[at..at + m], &ctx) {
            silent_at = Some(at + m);
            break;
        }
        at += m;
    }
    Run { out, silent_at }
}

fn run(name: &str, p: &[f32], e: &Event, n: usize, block: usize) -> Run {
    let mut v = make(name, RATE).unwrap_or_else(|| panic!("no voice for {name}"));
    run_on(v.as_mut(), p, e, n, block)
}

fn render(name: &str, p: &[f32], e: &Event, n: usize) -> Vec<[f32; 2]> {
    run(name, p, e, n, n).out
}

fn left(x: &[[f32; 2]]) -> Vec<f64> {
    x.iter().map(|s| s[0] as f64).collect()
}

fn rms(x: &[f64]) -> f64 {
    if x.is_empty() {
        return 0.0;
    }
    (x.iter().map(|v| v * v).sum::<f64>() / x.len() as f64).sqrt()
}

/// The mean frequency over `x` from its rising zero crossings, interpolated.
fn zc_hz(x: &[f64]) -> f64 {
    let mut first = None;
    let mut last = 0.0;
    let mut count = 0;
    for i in 1..x.len() {
        if x[i - 1] < 0.0 && x[i] >= 0.0 {
            let t = (i - 1) as f64 + x[i - 1] / (x[i - 1] - x[i]);
            if first.is_none() {
                first = Some(t);
            } else {
                count += 1;
            }
            last = t;
        }
    }
    let first = first.expect("no zero crossing");
    assert!(count > 0, "one zero crossing only");
    count as f64 * RATE as f64 / (last - first)
}

#[test]
fn make_knows_its_names_and_only_those() {
    for name in ALL {
        assert!(make(name, RATE).is_some(), "{name}");
    }
    assert!(make("kik", RATE).is_none());
    assert!(make("", RATE).is_none());
}

#[test]
fn every_voice_renders_a_finite_bounded_hit_that_decays() {
    for name in ALL {
        let e = hit(name, 3);
        let n = life(name, &e);
        let x = left(&render(name, &defaults(name), &e, n));
        assert!(x.iter().all(|v| v.is_finite()), "{name}: not finite");
        assert!(x.iter().all(|v| v.abs() <= 4.0), "{name}: out of bounds");
        let tenth = n / 10;
        let head = rms(&x[..tenth]);
        let tail = rms(&x[n - tenth..]);
        assert!(head > 1e-3, "{name}: silent ({head})");
        assert!(tail < 0.05 * head, "{name}: tail {tail} head {head}");
    }
}

#[test]
fn output_is_mono_centred() {
    for name in ALL {
        let e = hit(name, 3);
        let out = render(name, &defaults(name), &e, frames(0.2));
        assert!(out.iter().all(|s| s[0] == s[1]), "{name}");
    }
}

#[test]
fn bounded_and_finite_at_the_params_extremes_and_edges() {
    for name in ALL {
        for high in [false, true] {
            for (vel, decay) in [(1.0, 1.0), (0.0, 1.0), (1.0, 0.0), (1.0, 0.01)] {
                let mut e = hit(name, 9);
                e.vel = vel;
                e.decay = decay;
                let p = extreme(name, high);
                let n = life(name, &e).max(frames(0.1));
                let x = left(&render(name, &p, &e, n));
                assert!(
                    x.iter().all(|v| v.is_finite() && v.abs() <= 4.0),
                    "{name} high={high} vel={vel} decay={decay}"
                );
                if vel == 0.0 {
                    assert!(x.iter().all(|v| *v == 0.0), "{name}: sound at vel 0");
                }
            }
        }
    }
}

#[test]
fn a_voice_falls_silent_within_its_instruments_life() {
    // max_life_s is the longest a hit rings at the params' maxima, scaled by the step's decay
    for name in ALL {
        for decay in [1.0, 0.3] {
            let mut e = hit(name, 4);
            e.decay = decay;
            let p = extreme(name, true);
            let n = life(name, &e);
            let r = run(name, &p, &e, n + 512, 1);
            let at = r
                .silent_at
                .unwrap_or_else(|| panic!("{name} rings past its life"));
            assert!(
                at <= n + 1,
                "{name} decay={decay}: silent at {at}, life {n}"
            );
        }
    }
}

#[test]
fn block_size_does_not_change_the_samples() {
    for name in ALL {
        let mut e = hit(name, 5);
        e.vel = 0.7;
        let p = defaults(name);
        let n = life(name, &e);
        let whole = render(name, &p, &e, n);
        for block in [1, 7, 128] {
            assert_eq!(
                run(name, &p, &e, n, block).out,
                whole,
                "{name} block {block}"
            );
        }
        assert_eq!(render(name, &p, &e, n), whole, "{name}: second render");
    }
}

#[test]
fn start_resets_everything_from_the_last_event() {
    for name in ALL {
        let e = hit(name, 6);
        let p = defaults(name);
        let n = life(name, &e);
        let fresh = render(name, &p, &e, n);

        let mut other = hit(name, 77);
        other.vel = 0.3;
        other.note += 5.0;
        other.end = Some(frames(0.02) as u64);
        if is_note(name) {
            other.legato_from = Some(40.0);
        }
        let mut v = make(name, RATE).unwrap();
        // left mid-ring, then reused
        run_on(v.as_mut(), &extreme(name, true), &other, frames(0.01), 64);
        let reused = run_on(v.as_mut(), &p, &e, n, n).out;
        assert_eq!(reused, fresh, "{name}");
    }
}

#[test]
fn end_fades_out_within_5_ms_and_leaves_earlier_frames_alone() {
    for name in ALL {
        let p = defaults(name);
        let free = hit(name, 8);
        let cut_at = frames(0.02);
        let mut cut = free.clone();
        cut.end = Some(cut_at as u64);
        let n = life(name, &free);
        let a = left(&render(name, &p, &free, n));
        let r = run(name, &p, &cut, n, 1);
        let b = left(&r.out);
        assert_eq!(&a[..cut_at], &b[..cut_at], "{name}: frames before the cut");
        assert!(
            rms(&b[cut_at - 100..cut_at]) > 1e-4,
            "{name}: nothing to cut"
        );
        assert!(
            b[cut_at + FADE..].iter().all(|v| v.abs() < 1e-6),
            "{name}: sound after the fade"
        );
        let at = r.silent_at.expect("never reported silent");
        assert!(at <= cut_at + FADE + 1, "{name}: silent at {at}");
    }
}

#[test]
fn an_end_on_the_first_frame_is_silent_after_the_fade() {
    for name in ALL {
        let mut e = hit(name, 8);
        e.end = Some(0);
        let x = left(&render(name, &defaults(name), &e, frames(0.05)));
        assert!(x[FADE..].iter().all(|v| *v == 0.0), "{name}");
    }
}

#[test]
fn a_softer_hit_is_quieter() {
    for name in ALL {
        let p = defaults(name);
        let level = |vel: f32| {
            let mut e = hit(name, 2);
            e.vel = vel;
            rms(&left(&render(name, &p, &e, frames(0.1))))
        };
        let (soft, loud) = (level(0.4), level(0.8));
        assert!(soft < loud, "{name}: {soft} vs {loud}");
    }
}

#[test]
fn level_follows_velocity_to_the_power_1_5_in_the_linear_voices() {
    // the voices without a drive are linear in their input, so the level ratio is the gain's
    let want = 0.5f64.powf(1.5);
    for name in ["snare", "clap", "hat_closed", "hat_open", "rim", "cowbell"] {
        let p = defaults(name);
        let level = |vel: f32| {
            let mut e = hit(name, 2);
            e.vel = vel;
            rms(&left(&render(name, &p, &e, frames(0.1))))
        };
        let ratio = level(0.4) / level(0.8);
        assert!((ratio - want).abs() < 1e-4, "{name}: {ratio}");
    }
}

#[test]
fn noise_differs_between_events() {
    // the cowbell is two squares only
    for name in ALL.iter().filter(|n| **n != "cowbell") {
        let p = defaults(name);
        let n = frames(0.05);
        let a = render(name, &p, &hit(name, 1), n);
        let b = render(name, &p, &hit(name, 2), n);
        assert_ne!(a, b, "{name}: the same burst for two events");
    }
}

#[test]
fn kick_settles_on_its_tune_after_the_drop() {
    let tune = 50.0;
    let drop_ms = 20.0;
    let p = with(
        "kick",
        &[("tune", tune), ("drop_ms", drop_ms), ("decay", 2.0)],
    );
    let e = hit("kick", 1);
    let x = left(&render("kick", &p, &e, frames(1.0)));
    let from = frames(10.0 * drop_ms as f64 / 1000.0);
    let f = zc_hz(&x[from..]);
    assert!((f / tune as f64 - 1.0).abs() < 0.03, "{f} Hz");
}

#[test]
fn kick_starts_above_its_tune() {
    // 24 st above: four times the tune at the hit, still above twice it 5 ms on
    let tune = 50.0;
    let p = with(
        "kick",
        &[
            ("tune", tune),
            ("pitch_drop", 24.0),
            ("drop_ms", 40.0),
            ("click", 0.0),
        ],
    );
    let x = left(&render("kick", &p, &hit("kick", 1), frames(0.2)));
    let half_cycle = x[1..].iter().position(|v| *v < 0.0).unwrap() + 1;
    let f = RATE as f64 / (2.0 * half_cycle as f64);
    assert!(f > 2.0 * tune as f64, "{f} Hz");
}

#[test]
fn a_step_pitch_moves_a_drum_by_semitones() {
    let tune = 50.0;
    let p = with("kick", &[("tune", tune), ("drop_ms", 20.0), ("decay", 2.0)]);
    let mut e = hit("kick", 1);
    e.note = 72.0;
    let x = left(&render("kick", &p, &e, frames(1.0)));
    let f = zc_hz(&x[frames(0.2)..]);
    assert!((f / (2.0 * tune as f64) - 1.0).abs() < 0.03, "{f} Hz");
}

#[test]
fn bass808_plays_its_note() {
    let p = with("bass808", &[("drive", 0.0)]);
    let mut e = hit("bass808", 1);
    e.off = Some(frames(1.0) as u64);
    let x = left(&render("bass808", &p, &e, frames(1.0)));
    let f = zc_hz(&x[frames(0.05)..frames(0.95)]);
    assert!((f / 55.0 - 1.0).abs() < 0.005, "{f} Hz");
}

#[test]
fn bass808_tune_shifts_by_semitones() {
    let p = with("bass808", &[("drive", 0.0), ("tune", 12.0)]);
    let mut e = hit("bass808", 1);
    e.off = Some(frames(1.0) as u64);
    let x = left(&render("bass808", &p, &e, frames(1.0)));
    let f = zc_hz(&x[frames(0.05)..frames(0.95)]);
    assert!((f / 110.0 - 1.0).abs() < 0.005, "{f} Hz");
}

#[test]
fn bass808_glides_from_the_note_it_cut() {
    let glide_ms = 500.0;
    let p = with(
        "bass808",
        &[("drive", 0.0), ("glide_ms", glide_ms), ("decay", 8.0)],
    );
    let mut e = hit("bass808", 1);
    e.note = 45.0;
    e.legato_from = Some(33.0);
    e.off = Some(frames(1.0) as u64);
    let x = left(&render("bass808", &p, &e, frames(1.0)));

    // the first cycle, from the start's zero phase to the next rising crossing
    let next = (2..x.len())
        .find(|&i| x[i - 1] < 0.0 && x[i] >= 0.0)
        .unwrap();
    let first = RATE as f64 / next as f64;
    assert!(first > 50.0 && first < 66.0, "first cycle {first} Hz");

    let from = frames(glide_ms as f64 / 1000.0);
    let f = zc_hz(&x[from..from + frames(0.25)]);
    assert!((f / 110.0 - 1.0).abs() < 0.01, "after the glide {f} Hz");
}

#[test]
fn bass808_without_a_source_does_not_glide() {
    let p = with("bass808", &[("drive", 0.0), ("glide_ms", 500.0)]);
    let mut e = hit("bass808", 1);
    e.note = 45.0;
    e.off = Some(frames(1.0) as u64);
    let x = left(&render("bass808", &p, &e, frames(1.0)));
    let f = zc_hz(&x[frames(0.02)..frames(0.2)]);
    assert!((f / 110.0 - 1.0).abs() < 0.005, "{f} Hz");
}

#[test]
fn a_legato_note_has_no_click() {
    let quiet = with("bass808", &[("click", 0.0)]);
    let loud = with("bass808", &[("click", 1.0)]);
    let mut e = hit("bass808", 1);
    e.legato_from = Some(30.0);
    let n = frames(0.1);
    assert_eq!(
        render("bass808", &quiet, &e, n),
        render("bass808", &loud, &e, n)
    );
    e.legato_from = None;
    assert_ne!(
        render("bass808", &quiet, &e, n),
        render("bass808", &loud, &e, n)
    );
}

#[test]
fn bass808_release_begins_at_off() {
    let release = 0.05;
    let p = with("bass808", &[("release", release)]);
    let held = hit("bass808", 1);
    let off = held.off.unwrap() as usize;
    let mut longer = held.clone();
    longer.off = Some(frames(0.9) as u64);
    let a = left(&render("bass808", &p, &held, frames(1.0)));
    let b = left(&render("bass808", &p, &longer, frames(1.0)));
    assert_eq!(&a[..off], &b[..off]);
    assert!(rms(&a[off - 500..off]) > 1e-3);
    let gone = off + frames(release as f64) + FADE;
    assert!(a[gone..].iter().all(|v| v.abs() < 1e-6));
    assert!(rms(&b[gone..gone + 500]) > 1e-3);
}

/// The last frame above -60 dB of the peak.
fn rings(name: &str, p: &[f32], e: &Event) -> usize {
    let x = left(&render(name, p, e, life(name, e)));
    let peak = x.iter().fold(0.0f64, |m, v| m.max(v.abs()));
    x.iter().rposition(|v| v.abs() > 1e-3 * peak).unwrap()
}

#[test]
fn hat_open_rings_longer_than_hat_closed() {
    let closed = rings(
        "hat_closed",
        &extreme("hat_closed", true),
        &hit("hat_closed", 1),
    );
    let open = rings(
        "hat_open",
        &with("hat_open", &[("decay", 2.0)]),
        &hit("hat_open", 1),
    );
    assert!(open > 2 * closed, "open {open} closed {closed}");
}

#[test]
fn a_shorter_step_decay_shortens_the_hit() {
    for name in DRUMS {
        let p = defaults(name);
        let full = hit(name, 1);
        let mut short = full.clone();
        short.decay = 0.25;
        let (a, b) = (rings(name, &p, &full), rings(name, &p, &short));
        assert!(b < a, "{name}: {b} vs {a}");
    }
}
