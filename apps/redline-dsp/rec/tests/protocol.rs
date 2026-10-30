use redline_rec::protocol::{
    format, format_wrong_rate, parse, Cause, Command, Dropout, Meters, Reply, TakeReport,
};
use serde_json::{json, Value};
use std::path::PathBuf;

#[test]
fn each_command_parses_from_its_line() {
    assert_eq!(
        parse(r#"{"cmd": "start", "path": "takes/take-01.wav", "offset": 312}"#).unwrap(),
        Command::Start {
            path: PathBuf::from("takes/take-01.wav"),
            offset: 312
        }
    );
    assert_eq!(parse(r#"{"cmd": "stop"}"#).unwrap(), Command::Stop);
    assert_eq!(parse(r#"{"cmd": "meters"}"#).unwrap(), Command::Meters);
    assert_eq!(parse(r#"{"cmd":"quit"}"#).unwrap(), Command::Quit);
    let rack = json!({"effects": [{"effect": "gain"}]});
    assert_eq!(
        parse(&json!({"cmd": "set_rack", "rack": rack, "budget": 96}).to_string()).unwrap(),
        Command::SetRack {
            rack: rack.clone(),
            budget: Some(96)
        }
    );
    assert_eq!(
        parse(&json!({"cmd": "set_rack", "rack": rack}).to_string()).unwrap(),
        Command::SetRack { rack, budget: None }
    );
}

#[test]
fn a_wrong_line_is_an_error_naming_what_is_wrong() {
    let err = |line: &str| parse(line).unwrap_err();
    assert!(err(r#"{"cmd": "record"}"#).contains("record"));
    assert!(err(r#"{"cmd": "start", "path": "a.wav"}"#).contains("offset"));
    assert!(err(r#"{"cmd": "start", "path": "a.wav", "offset": -1}"#).contains("-1"));
    assert!(err(r#"{"cmd": "start", "path": "a.wav", "offset": 1, "of": 2}"#).contains("of"));
    assert!(err(r#"{"cmd": "stop", "now": true}"#).contains("now"));
    assert!(err(r#"{"path": "a.wav"}"#).contains("cmd"));
    assert!(!err("start").is_empty());
    assert!(!err("").is_empty());
}

fn line(reply: &Reply) -> Value {
    let text = format(reply);
    assert!(!text.contains('\n'), "one line: {text}");
    serde_json::from_str(&text).unwrap()
}

#[test]
fn replies_are_one_json_line_each_naming_the_command() {
    assert_eq!(
        line(&Reply::Start {
            path: PathBuf::from("t.wav")
        }),
        json!({"reply": "start", "path": "t.wav"})
    );
    assert_eq!(
        line(&Reply::SetRack {
            latency: 96,
            dropped: vec!["limiter".into()]
        }),
        json!({"reply": "set_rack", "latency": 96, "dropped": ["limiter"]})
    );
    assert_eq!(line(&Reply::Quit), json!({"reply": "quit"}));
    assert_eq!(
        line(&Reply::Error {
            error: "no take is being recorded".into()
        }),
        json!({"reply": "error", "error": "no take is being recorded"})
    );
    let meters = Meters {
        mic_peak: 0.5,
        out_peak: 0.25,
        xruns: 2,
        overflows: 1,
        recording: true,
        take_frames: 4800,
        rack_latency: 64,
        quantum: Some(128),
    };
    assert_eq!(
        line(&Reply::Meters(meters)),
        json!({"reply": "meters", "mic_peak": 0.5, "out_peak": 0.25, "xruns": 2,
               "overflows": 1, "recording": true, "take_frames": 4800, "rack_latency": 64,
               "quantum": 128})
    );
}

#[test]
fn a_take_report_carries_its_dropouts_for_the_sidecar() {
    let take = TakeReport {
        path: PathBuf::from("take-02.wav"),
        rate: 48000,
        frames: 96000,
        offset: 300,
        dropouts: vec![
            Dropout {
                start: 4800,
                frames: 128,
                causes: vec![Cause::Xrun, Cause::CounterGap],
            },
            Dropout {
                start: 9000,
                frames: 64,
                causes: vec![Cause::Overflow],
            },
        ],
    };
    let reply = Reply::Stop { take: take.clone() };
    assert_eq!(
        line(&reply),
        json!({"reply": "stop", "take": {
        "path": "take-02.wav", "rate": 48000, "frames": 96000, "offset": 300,
        "dropouts": [
            {"start": 4800, "frames": 128, "causes": ["xrun", "counter_gap"]},
            {"start": 9000, "frames": 64, "causes": ["overflow"]}
        ]}})
    );
    let back: TakeReport = serde_json::from_value(line(&reply)["take"].clone()).unwrap();
    assert_eq!(back, take);
}

#[test]
fn a_beat_at_another_rate_is_one_line_with_both_rates() {
    let text = format_wrong_rate(48000, 44100);
    assert!(!text.contains('\n'));
    let line: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(
        line,
        json!({"event": "wrong_rate", "rate": 48000, "beat_rate": 44100})
    );
}
