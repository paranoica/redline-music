use redline_dsp_core::effect::Rack;
use redline_rec::engine::{engine, serve, Config, Control, Engine, FADE_SECONDS};
use redline_rec::protocol::{Cause, Command, Dropout, Reply, TakeReport};
use redline_rec::wav::read;
use std::path::PathBuf;
use std::time::Duration;

const RATE: u32 = 48000;

fn noise(n: usize, seed: f64) -> Vec<f32> {
    (0..n)
        .map(|i| ((((i as f64 + seed) * 78.233).sin() * 43758.5453).fract() as f32) * 0.8)
        .collect()
}

fn beat(n: usize) -> Vec<[f32; 2]> {
    let l = noise(n, 0.31);
    let r = noise(n, 0.77);
    l.into_iter()
        .zip(r)
        .map(|(a, b)| [0.5 * a, 0.25 * b])
        .collect()
}

fn take_path(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("rec-engine");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    let _ = std::fs::remove_file(&path);
    path
}

fn setup(beat_frames: usize) -> (Engine, Control) {
    engine(Config::new(RATE, beat(beat_frames))).unwrap()
}

/// One cycle over `mic[at..at + n]`, the frame counter `start + at`; the outputs.
fn cycle(e: &mut Engine, mic: &[f32], at: usize, n: usize, start: u64) -> Vec<[f32; 2]> {
    let mut l = vec![9.0; n];
    let mut r = vec![9.0; n];
    e.cycle(&mic[at..at + n], &mut l, &mut r, start + at as u64);
    l.into_iter().zip(r).map(|(a, b)| [a, b]).collect()
}

fn finish(e: &mut Engine, c: &mut Control, at: u64) -> TakeReport {
    c.request_stop().unwrap();
    let mut l = [0.0; 64];
    let mut r = [0.0; 64];
    e.cycle(&[0.0; 64], &mut l, &mut r, at);
    c.wait_take(Duration::from_secs(10)).unwrap()
}

fn samples(path: &PathBuf) -> Vec<f32> {
    let wav = read(path).unwrap();
    assert_eq!((wav.rate, wav.channels), (RATE, 1));
    std::fs::remove_file(path).unwrap();
    wav.samples
}

#[test]
fn the_dry_take_is_the_mic_from_the_start_on_shifted_by_the_round_trip() {
    let start = 1_000_000u64;
    for (offset, late_cycles) in [(0u64, 0usize), (37, 0), (200, 0), (37, 10)] {
        let (mut e, mut c) = setup(48000);
        let mic = noise(6400, 0.5);
        let path = take_path(&format!("dry-{offset}-{late_cycles}.wav"));
        for k in 0..late_cycles {
            cycle(&mut e, &mic, k * 64, 64, start);
        }
        c.start(&path, offset).unwrap();
        for k in late_cycles..100 {
            cycle(&mut e, &mic, k * 64, 64, start);
        }
        let take = finish(&mut e, &mut c, start + 6400);
        let from = late_cycles * 64 + offset as usize;
        assert_eq!(
            take,
            TakeReport {
                path: path.clone(),
                rate: RATE,
                frames: (6400 - from) as u64,
                offset,
                dropouts: vec![],
            }
        );
        assert_eq!(samples(&path), &mic[from..], "offset {offset}");
    }
}

const MONITOR: &str = r#"{"effects": [
    {"effect": "compressor"},
    {"effect": "pan", "params": {"pan": 0.5}},
    {"effect": "delay"}
]}"#;

fn rack_alone(json: &str, mic: &[f32], block: usize) -> Vec<[f32; 2]> {
    let mut rack = Rack::from_json(json).unwrap();
    rack.reset(RATE as f32).unwrap();
    let mut y: Vec<[f32; 2]> = mic.iter().map(|&m| [m, m]).collect();
    for chunk in y.chunks_mut(block) {
        rack.process(chunk);
    }
    y
}

#[test]
fn the_monitor_is_the_rack_on_the_mic_faded_in_plus_the_beat() {
    let n = 9600;
    let mic = noise(n, 0.9);
    let beat = beat(4800);
    let (mut e, mut c) = setup(4800);
    c.set_rack_json(MONITOR, None).unwrap();
    let path = take_path("monitor.wav");
    c.start(&path, 0).unwrap();
    let mut out = Vec::new();
    for k in 0..n / 64 {
        out.extend(cycle(&mut e, &mic, k * 64, 64, 77));
    }
    let wet = rack_alone(MONITOR, &mic, 64);
    let fade = (FADE_SECONDS * RATE as f32) as usize;
    assert_eq!(fade, 240);
    for i in 0..n {
        let g = (i as f32 / fade as f32).min(1.0);
        for ch in 0..2 {
            let monitor = (1.0 - g) * mic[i] + g * wet[i][ch];
            let want = monitor + beat.get(i).map_or(0.0, |f| f[ch]);
            assert!(
                (out[i][ch] - want).abs() < 1e-5,
                "frame {i} channel {ch}: {} against {want}",
                out[i][ch]
            );
        }
    }
    finish(&mut e, &mut c, 77 + n as u64);
    std::fs::remove_file(&path).unwrap();
}

#[test]
fn without_a_take_the_monitor_plays_alone() {
    let mic = noise(640, 0.2);
    let (mut e, _c) = setup(48000);
    for k in 0..10 {
        let out = cycle(&mut e, &mic, k * 64, 64, 0);
        for (i, f) in out.iter().enumerate() {
            assert_eq!(*f, [mic[k * 64 + i]; 2]);
        }
    }
}

fn settle(e: &mut Engine, cycles: usize) {
    let mut l = [0.0; 64];
    let mut r = [0.0; 64];
    for _ in 0..cycles {
        e.cycle(&[0.1; 64], &mut l, &mut r, 0);
    }
}

#[test]
fn a_swapped_out_rack_comes_back_to_the_control_side_after_the_fade() {
    let (mut e, mut c) = setup(48000);
    c.set_rack_json(MONITOR, None).unwrap();
    settle(&mut e, 1);
    assert_eq!(c.collect(), 0, "the first rack still fades out");
    settle(&mut e, 4);
    assert_eq!(c.collect(), 1);
    c.set_rack_json(r#"{"effects": [{"effect": "gain"}]}"#, None)
        .unwrap();
    settle(&mut e, 5);
    assert_eq!(c.collect(), 1);
    settle(&mut e, 5);
    assert_eq!(c.collect(), 0);
}

#[test]
fn racks_sent_faster_than_the_engine_fades_are_refused_then_taken_again() {
    let (mut e, mut c) = setup(48000);
    let gain =
        |db: i32| format!(r#"{{"effects": [{{"effect": "gain", "params": {{"db": {db}}}}}]}}"#);
    for db in 1..=3 {
        c.set_rack_json(&gain(db), None).unwrap();
    }
    let err = c.set_rack_json(&gain(4), None).unwrap_err();
    assert!(err.contains("busy"), "{err}");
    // of racks waiting together only the newest is played: the two before it come back
    // unplayed at once, the first rack after the fade
    settle(&mut e, 1);
    assert_eq!(c.collect(), 2);
    settle(&mut e, 20);
    assert_eq!(c.collect(), 1);
    let mut l = [0.0; 64];
    let mut r = [0.0; 64];
    e.cycle(&[0.5; 64], &mut l, &mut r, 0);
    let g = 10f32.powf(3.0 / 20.0);
    assert!(
        (l[0] - 0.5 * g).abs() < 1e-6,
        "the last rack plays: {}",
        l[0]
    );
    c.set_rack_json(&gain(4), None).unwrap();
}

#[test]
fn a_rack_over_the_budget_loses_effects_from_its_end() {
    let (_e, mut c) = setup(48000);
    let json = r#"{"effects": [
        {"effect": "limiter", "params": {"lookahead": 1}},
        {"effect": "limiter", "params": {"lookahead": 2}}
    ]}"#;
    assert_eq!(
        c.set_rack_json(json, Some(100)).unwrap(),
        (48, vec!["limiter"])
    );
    assert_eq!(c.set_rack_json(json, None).unwrap(), (144, vec![]));
    assert_eq!(c.meters().rack_latency, 144);
    let err = c.set_rack_json(r#"{"effects": [{"effect": "nope"}]}"#, None);
    assert!(err.unwrap_err().contains("nope"));
}

#[test]
fn a_rack_sent_without_a_budget_is_held_to_the_default_one_and_a_sent_budget_wins() {
    let (_e, mut c) = setup(48000);
    let rack: serde_json::Value = serde_json::from_str(
        r#"{"effects": [
            {"effect": "limiter", "params": {"lookahead": 1}},
            {"effect": "limiter", "params": {"lookahead": 2}}
        ]}"#,
    )
    .unwrap();
    let set = |c: &mut Control, budget: Option<usize>| {
        c.handle(Command::SetRack {
            rack: rack.clone(),
            budget,
        })
    };
    // no default yet: the whole rack plays
    assert_eq!(
        set(&mut c, None),
        Reply::SetRack {
            latency: 144,
            dropped: vec![]
        }
    );
    c.set_default_budget(Some(100));
    assert_eq!(
        set(&mut c, None),
        Reply::SetRack {
            latency: 48,
            dropped: vec!["limiter".into()]
        }
    );
    assert_eq!(
        set(&mut c, Some(47)),
        Reply::SetRack {
            latency: 0,
            dropped: vec!["limiter".into(), "limiter".into()]
        }
    );
}

/// Cycles of 64 frames at the counter `start + at` for each `at`, over `mic` indexed by the
/// counter from `start`; the take with its report.
fn record(
    offset: u64,
    ats: &[(usize, usize)],
    xrun_before: &[usize],
    config: Config,
    name: &str,
) -> (TakeReport, Vec<f32>, Vec<f32>, Control) {
    let start = 5000u64;
    let end = ats.iter().map(|(at, n)| at + n).max().unwrap();
    let mic = noise(end, 0.4);
    let (mut e, mut c) = engine(config).unwrap();
    let xruns = e.xrun_reporter();
    let path = take_path(name);
    c.start(&path, offset).unwrap();
    for &(at, n) in ats {
        if xrun_before.contains(&at) {
            xruns.report();
        }
        cycle(&mut e, &mic, at, n, start);
    }
    let take = finish(&mut e, &mut c, start + end as u64);
    let s = samples(&path);
    (take, s, mic, c)
}

fn blocks(from: usize, to: usize) -> Vec<(usize, usize)> {
    (from..to).step_by(64).map(|at| (at, 64)).collect()
}

#[test]
fn a_jump_of_the_frame_counter_is_silence_of_its_length_in_the_take() {
    let mut ats = blocks(0, 128);
    ats.extend(blocks(320, 640));
    let (take, s, mic, _c) = record(10, &ats, &[], Config::new(RATE, beat(10)), "jump.wav");
    assert_eq!(
        take.dropouts,
        [Dropout {
            start: 118,
            frames: 192,
            causes: vec![Cause::CounterGap]
        }]
    );
    assert_eq!(take.frames, 630);
    assert_eq!(s.len(), 630);
    assert_eq!(&s[..118], &mic[10..128]);
    assert!(s[118..310].iter().all(|&v| v == 0.0));
    assert_eq!(&s[310..], &mic[320..]);
}

#[test]
fn an_xrun_is_logged_with_its_gap_and_one_that_lost_nothing_without() {
    let mut ats = blocks(0, 256);
    ats.extend(blocks(384, 640));
    let (take, s, mic, c) = record(
        0,
        &ats,
        &[192, 384],
        Config::new(RATE, beat(10)),
        "xrun.wav",
    );
    assert_eq!(
        take.dropouts,
        [
            Dropout {
                start: 192,
                frames: 0,
                causes: vec![Cause::Xrun]
            },
            Dropout {
                start: 256,
                frames: 128,
                causes: vec![Cause::Xrun, Cause::CounterGap]
            }
        ]
    );
    assert_eq!(&s[..256], &mic[..256]);
    assert!(s[256..384].iter().all(|&v| v == 0.0));
    assert_eq!(&s[384..], &mic[384..]);
    let mut c = c;
    assert_eq!(c.meters().xruns, 2);
}

#[test]
fn a_gap_before_the_round_trip_is_over_costs_the_take_only_what_falls_inside_it() {
    // frames 64..150 are lost; the take starts at the mic's frame 100
    let mut ats = blocks(0, 64);
    ats.push((150, 64));
    ats.extend(blocks(214, 470));
    let (take, s, mic, _c) = record(100, &ats, &[], Config::new(RATE, beat(10)), "early.wav");
    assert_eq!(
        take.dropouts,
        [Dropout {
            start: 0,
            frames: 50,
            causes: vec![Cause::CounterGap]
        }]
    );
    assert!(s[..50].iter().all(|&v| v == 0.0));
    assert_eq!(&s[50..], &mic[150..]);
    // a gap all before it costs nothing
    let mut ats = blocks(0, 64);
    ats.extend(blocks(128, 512));
    let (take, s, mic, _c) = record(200, &ats, &[], Config::new(RATE, beat(10)), "before.wav");
    assert!(take.dropouts.is_empty(), "{:?}", take.dropouts);
    assert_eq!(&s[..], &mic[200..]);
}

#[test]
fn a_block_the_ring_has_no_room_for_is_silence_in_the_take() {
    let mut config = Config::new(RATE, beat(10));
    config.ring_frames = 4096;
    // ten blocks fit even if the writer never ran; 8192 frames never fit
    let mut ats = blocks(0, 640);
    ats.push((640, 8192));
    ats.extend(blocks(8832, 9472));
    let (take, s, mic, mut c) = record(5, &ats, &[], config, "overflow.wav");
    assert_eq!(
        take.dropouts,
        [Dropout {
            start: 635,
            frames: 8192,
            causes: vec![Cause::Overflow]
        }]
    );
    assert_eq!(&s[..635], &mic[5..640]);
    assert!(s[635..8827].iter().all(|&v| v == 0.0));
    assert_eq!(&s[8827..], &mic[8832..]);
    assert_eq!(c.meters().overflows, 1);
}

#[test]
fn a_take_ending_in_an_overflow_is_padded_to_its_end() {
    let mut config = Config::new(RATE, beat(10));
    config.ring_frames = 4096;
    let mut ats = blocks(0, 128);
    ats.push((128, 8192));
    let (take, s, mic, _c) = record(0, &ats, &[], config, "tail.wav");
    assert_eq!(take.frames, 8320);
    assert_eq!(
        take.dropouts,
        [Dropout {
            start: 128,
            frames: 8192,
            causes: vec![Cause::Overflow]
        }]
    );
    assert_eq!(&s[..128], &mic[..128]);
    assert_eq!(s.len(), 8320);
    assert!(s[128..].iter().all(|&v| v == 0.0));
}

#[test]
fn the_control_side_refuses_what_makes_no_sense_and_says_why() {
    let (mut e, mut c) = setup(48000);
    assert!(c.request_stop().unwrap_err().contains("no take"));
    let existing = take_path("existing.wav");
    std::fs::write(&existing, b"keep").unwrap();
    assert!(c.start(&existing, 0).unwrap_err().contains("exists"));
    assert_eq!(std::fs::read(&existing).unwrap(), b"keep");
    std::fs::remove_file(&existing).unwrap();
    let missing = take_path("no-such-dir").join("t.wav");
    assert!(c.start(&missing, 0).is_err());
    let path = take_path("twice.wav");
    c.start(&path, 0).unwrap();
    assert!(c
        .start(&take_path("other.wav"), 0)
        .unwrap_err()
        .contains("already"));
    match c.handle(Command::Start {
        path: take_path("other.wav"),
        offset: 0,
    }) {
        Reply::Error { error } => assert!(error.contains("already")),
        other => panic!("{other:?}"),
    }
    settle(&mut e, 1);
    finish(&mut e, &mut c, 64);
    std::fs::remove_file(&path).unwrap();
}

#[test]
fn meters_hold_the_peaks_since_they_were_last_read() {
    let (mut e, mut c) = setup(48000);
    let mut mic = vec![0.1f32; 64];
    mic[10] = -0.5;
    let mut l = [0.0; 64];
    let mut r = [0.0; 64];
    e.cycle(&mic, &mut l, &mut r, 0);
    e.cycle(&[0.2; 64], &mut l, &mut r, 64);
    let m = c.meters();
    assert_eq!((m.mic_peak, m.out_peak), (0.5, 0.5));
    assert!(!m.recording);
    e.cycle(&[0.2; 64], &mut l, &mut r, 128);
    assert_eq!(c.meters().mic_peak, 0.2);
    assert_eq!(c.meters().mic_peak, 0.0);
    let path = take_path("meters.wav");
    c.start(&path, 0).unwrap();
    e.cycle(&[0.2; 64], &mut l, &mut r, 192);
    let m = c.meters();
    assert!(m.recording);
    assert_eq!(m.take_frames, 64);
    finish(&mut e, &mut c, 256);
    std::fs::remove_file(&path).unwrap();
}

#[test]
fn the_protocol_loop_answers_each_line_and_stops_at_quit() {
    let (_e, mut c) = setup(48000);
    let input = [
        r#"{"cmd": "meters"}"#,
        "not json",
        r#"{"cmd": "set_rack", "rack": {"effects": [{"effect": "limiter", "params": {"lookahead": 1}}]}, "budget": 10}"#,
        "",
        r#"{"cmd": "stop"}"#,
        r#"{"cmd": "quit"}"#,
        r#"{"cmd": "meters"}"#,
    ]
    .join("\n");
    let mut out = Vec::new();
    serve(&mut c, input.as_bytes(), &mut out).unwrap();
    let replies: Vec<Reply> = String::from_utf8(out)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(replies.len(), 5, "{replies:?}");
    assert!(matches!(replies[0], Reply::Meters(_)));
    assert!(matches!(replies[1], Reply::Error { .. }));
    assert_eq!(
        replies[2],
        Reply::SetRack {
            latency: 0,
            dropped: vec!["limiter".into()]
        }
    );
    assert!(matches!(&replies[3], Reply::Error { error } if error.contains("no take")));
    assert_eq!(replies[4], Reply::Quit);
}
