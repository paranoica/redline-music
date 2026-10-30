use redline_rec::backend::{
    beat_frames, latency_request, parse_args, pick_ports, startup, Args, Connections, FrameClock,
    Graph,
};
use redline_rec::protocol::{format_startup, RoundTripSource};
use redline_rec::wav::Wav;
use serde_json::{json, Value};
use std::path::PathBuf;

fn args(line: &[&str]) -> Result<Args, String> {
    parse_args(line.iter().map(|s| s.to_string()))
}

#[test]
fn the_beat_alone_takes_the_defaults() {
    let a = args(&["beat.wav"]).unwrap();
    assert_eq!(
        a,
        Args {
            beat: PathBuf::from("beat.wav"),
            mic: None,
            left: None,
            right: None,
            connect: true,
            round_trip: None,
            quantum: 64,
            name: "redline-rec".into(),
        }
    );
}

#[test]
fn every_flag_sets_its_field_in_any_order() {
    let a = args(&[
        "--mic",
        "card:capture_2",
        "--right",
        "card:playback_4",
        "b.wav",
        "--left",
        "card:playback_3",
        "--round-trip",
        "312",
        "--quantum",
        "128",
        "--name",
        "rec-test",
    ])
    .unwrap();
    assert_eq!(a.beat, PathBuf::from("b.wav"));
    assert_eq!(a.mic.as_deref(), Some("card:capture_2"));
    assert_eq!(a.left.as_deref(), Some("card:playback_3"));
    assert_eq!(a.right.as_deref(), Some("card:playback_4"));
    assert_eq!((a.round_trip, a.quantum), (Some(312), 128));
    assert_eq!(a.name, "rec-test");
    assert!(a.connect);
    assert!(!args(&["b.wav", "--no-connect"]).unwrap().connect);
    assert_eq!(
        args(&["b.wav", "--round-trip", "0"]).unwrap().round_trip,
        Some(0)
    );
}

#[test]
fn a_wrong_command_line_is_refused_naming_what_is_wrong() {
    let err = |line: &[&str]| args(line).unwrap_err();
    assert!(err(&[]).contains("beat"));
    assert!(err(&["a.wav", "b.wav"]).contains("b.wav"));
    assert!(err(&["a.wav", "--loud"]).contains("--loud"));
    assert!(err(&["a.wav", "--mic"]).contains("--mic"));
    assert!(err(&["a.wav", "--round-trip", "-3"]).contains("-3"));
    assert!(err(&["a.wav", "--round-trip", "soon"]).contains("soon"));
    assert!(err(&["a.wav", "--quantum", "0"]).contains("--quantum"));
    assert!(err(&["a.wav", "--quantum", "8193"]).contains("--quantum"));
    assert!(err(&["a.wav", "--mic", "x", "--no-connect"]).contains("--no-connect"));
}

#[test]
fn the_latency_is_asked_for_only_when_the_user_has_not_set_one() {
    assert_eq!(
        latency_request(None, None, 64, 48000).as_deref(),
        Some("64/48000")
    );
    assert_eq!(
        latency_request(None, None, 128, 44100).as_deref(),
        Some("128/44100")
    );
    assert_eq!(latency_request(Some("256/48000"), None, 64, 48000), None);
    assert_eq!(latency_request(None, Some("32/48000"), 64, 48000), None);
    // an empty variable is not a setting
    assert_eq!(
        latency_request(Some(""), Some(""), 64, 48000).as_deref(),
        Some("64/48000")
    );
}

#[test]
fn the_frame_clock_runs_on_past_the_32_bit_wrap_and_keeps_gaps() {
    let mut c = FrameClock::default();
    assert_eq!(c.frame(1000), 1000);
    assert_eq!(c.frame(1064), 1064);
    // a skipped cycle stays a jump
    assert_eq!(c.frame(1192), 1192);
    let mut c = FrameClock::default();
    let near = u32::MAX - 63;
    assert_eq!(c.frame(near), near as u64);
    assert_eq!(c.frame(0), near as u64 + 64);
    assert_eq!(c.frame(64), near as u64 + 128);
}

fn ports(names: &[&str]) -> Vec<String> {
    names.iter().map(|s| s.to_string()).collect()
}

#[test]
fn the_first_physical_ports_are_taken_unless_named() {
    let caps = ports(&["system:capture_1", "system:capture_2"]);
    let plays = ports(&[
        "system:playback_1",
        "system:playback_2",
        "system:playback_3",
    ]);
    let a = args(&["b.wav"]).unwrap();
    assert_eq!(
        pick_ports(&a, &caps, &plays).unwrap(),
        Connections {
            mic: Some("system:capture_1".into()),
            left: Some("system:playback_1".into()),
            right: Some("system:playback_2".into()),
        }
    );
    let a = args(&[
        "b.wav",
        "--mic",
        "usb:capture_2",
        "--right",
        "usb:playback_9",
    ])
    .unwrap();
    assert_eq!(
        pick_ports(&a, &caps, &plays).unwrap(),
        Connections {
            mic: Some("usb:capture_2".into()),
            left: Some("system:playback_1".into()),
            right: Some("usb:playback_9".into()),
        }
    );
    // one output: both sides go to it
    let one = ports(&["system:playback_1"]);
    let a = args(&["b.wav"]).unwrap();
    let c = pick_ports(&a, &caps, &one).unwrap();
    assert_eq!(c.left, c.right);
    assert_eq!(c.left.as_deref(), Some("system:playback_1"));
}

#[test]
fn without_a_mic_or_an_output_the_backend_says_which_is_missing() {
    let caps = ports(&["system:capture_1"]);
    let plays = ports(&["system:playback_1", "system:playback_2"]);
    let a = args(&["b.wav"]).unwrap();
    assert!(pick_ports(&a, &[], &plays)
        .unwrap_err()
        .contains("microphone"));
    assert!(pick_ports(&a, &caps, &[]).unwrap_err().contains("output"));
    // a named mic needs no physical one
    let a = args(&["b.wav", "--mic", "usb:capture_1"]).unwrap();
    assert_eq!(
        pick_ports(&a, &[], &plays).unwrap().mic.as_deref(),
        Some("usb:capture_1")
    );
    // not connecting needs nothing
    let a = args(&["b.wav", "--no-connect"]).unwrap();
    assert_eq!(
        pick_ports(&a, &[], &[]).unwrap(),
        Connections {
            mic: None,
            left: None,
            right: None
        }
    );
}

#[test]
fn the_beat_must_be_at_the_graph_rate_and_mono_plays_on_both_sides() {
    let stereo = Wav {
        rate: 48000,
        channels: 2,
        samples: vec![0.1, 0.2, 0.3, 0.4],
    };
    assert_eq!(
        beat_frames(&stereo, 48000).unwrap(),
        vec![[0.1, 0.2], [0.3, 0.4]]
    );
    let mono = Wav {
        rate: 48000,
        channels: 1,
        samples: vec![0.5, -0.5],
    };
    assert_eq!(
        beat_frames(&mono, 48000).unwrap(),
        vec![[0.5, 0.5], [-0.5, -0.5]]
    );
    let err = beat_frames(&stereo, 44100).unwrap_err();
    assert!(err.contains("48000") && err.contains("44100"), "{err}");
    let wide = Wav {
        rate: 48000,
        channels: 3,
        samples: vec![0.0; 6],
    };
    assert!(beat_frames(&wide, 48000).unwrap_err().contains('3'));
}

fn graph() -> Graph {
    Graph {
        rate: 48000,
        quantum: 64,
        capture: (64, 64),
        playback: (128, 192),
    }
}

fn wired() -> Connections {
    Connections {
        mic: Some("system:capture_1".into()),
        left: Some("system:playback_1".into()),
        right: Some("system:playback_2".into()),
    }
}

#[test]
fn the_startup_line_reports_the_graph_and_labels_the_round_trip_an_estimate() {
    let mut s = startup(
        "redline-rec",
        &graph(),
        64,
        Some("64/48000".into()),
        None,
        wired(),
        96000,
    );
    assert!(s.mic_aliases.is_empty() && s.left_aliases.is_empty() && s.right_aliases.is_empty());
    // the backend adds what JACK reports for the connected ports
    s.mic_aliases = vec!["alsa_pcm:Card/capture_1".into()];
    assert_eq!(s.round_trip, 256);
    assert_eq!(s.round_trip_source, RoundTripSource::PortLatencyEstimate);
    assert_eq!(s.monitor_budget, 480 - 256);
    assert!(s.under_10ms);
    let text = format_startup(&s);
    assert!(!text.contains('\n'));
    let line: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(
        line,
        json!({
            "event": "started",
            "client": "redline-rec",
            "rate": 48000,
            "quantum": 64,
            "requested_quantum": 64,
            "latency_request": "64/48000",
            "capture_latency": [64, 64],
            "playback_latency": [128, 192],
            "round_trip": 256,
            "round_trip_ms": 256.0 / 48.0,
            "round_trip_source": "port_latency_estimate",
            "under_10ms": true,
            "monitor_budget": 224,
            "mic": "system:capture_1",
            "left": "system:playback_1",
            "right": "system:playback_2",
            "mic_aliases": ["alsa_pcm:Card/capture_1"],
            "left_aliases": [],
            "right_aliases": [],
            "beat_frames": 96000
        })
    );
}

#[test]
fn a_calibrated_round_trip_wins_over_the_estimate_and_over_10_ms_leaves_no_budget() {
    let s = startup("redline-rec", &graph(), 64, None, Some(300), wired(), 10);
    assert_eq!(s.round_trip, 300);
    assert_eq!(s.round_trip_source, RoundTripSource::Calibrated);
    assert_eq!(s.monitor_budget, 180);
    // the ports still say what they report
    assert_eq!(s.playback_latency, [128, 192]);
    let slow = Graph {
        quantum: 1024,
        capture: (1024, 1024),
        playback: (2048, 2048),
        ..graph()
    };
    let s = startup("redline-rec", &slow, 64, None, None, wired(), 10);
    assert_eq!((s.round_trip, s.monitor_budget), (3072, 0));
    assert!(!s.under_10ms);
    // exactly 10 ms is not under it
    let s = startup("redline-rec", &graph(), 64, None, Some(480), wired(), 10);
    assert!(!s.under_10ms);
    assert_eq!(s.monitor_budget, 0);
}
