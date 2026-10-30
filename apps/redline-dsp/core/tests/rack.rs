use redline_dsp_core::effect::{effects_json, make, Rack, EFFECTS};

fn noise(n: usize) -> Vec<[f32; 2]> {
    (0..n)
        .map(|i| {
            let r = |k: f64| (((i as f64 + k) * 78.233).sin() * 43758.5453).fract() as f32 * 0.5;
            [r(0.0), r(0.25)]
        })
        .collect()
}

fn render(json: &str, x: &[[f32; 2]]) -> Vec<[f32; 2]> {
    let mut rack = Rack::from_json(json).unwrap();
    rack.reset(48000.0).unwrap();
    let mut y = x.to_vec();
    for chunk in y.chunks_mut(128) {
        rack.process(chunk);
    }
    y
}

fn error(json: &str) -> String {
    match Rack::from_json(json) {
        Ok(_) => panic!("accepted {json}"),
        Err(e) => e.to_string(),
    }
}

#[test]
fn a_gain_rack_scales_by_its_decibels() {
    let x = noise(1000);
    let y = render(
        r#"{"effects": [{"effect": "gain", "on": true, "params": {"db": -3}}]}"#,
        &x,
    );
    let g = 10f32.powf(-3.0 / 20.0);
    assert!(x
        .iter()
        .zip(&y)
        .all(|(a, b)| (b[0] - a[0] * g).abs() < 1e-6));
}

#[test]
fn effects_run_in_the_rack_order() {
    let x = noise(500);
    let g = 10f32.powf(12.0 / 20.0);
    let gain = r#"{"effect": "gain", "params": {"db": 12}}"#;
    let sat = r#"{"effect": "saturate"}"#;
    let y = render(&format!(r#"{{"effects": [{gain}, {sat}]}}"#), &x);
    assert!(x
        .iter()
        .zip(&y)
        .all(|(a, b)| (b[0] - (g * a[0]).tanh()).abs() < 1e-6));
    let y = render(&format!(r#"{{"effects": [{sat}, {gain}]}}"#), &x);
    assert!(x
        .iter()
        .zip(&y)
        .all(|(a, b)| (b[0] - g * a[0].tanh()).abs() < 1e-5));
}

#[test]
fn an_effect_turned_off_is_skipped_and_an_empty_rack_changes_nothing() {
    let x = noise(300);
    let off = r#"{"effects": [{"effect": "gain", "on": false, "params": {"db": -60}}]}"#;
    assert_eq!(render(off, &x), x);
    assert_eq!(render(r#"{"effects": []}"#, &x), x);
}

#[test]
fn a_choice_is_given_by_its_name() {
    let x = noise(300);
    let by_name = r#"{"effects": [{"effect": "eq", "params": {"kind": "highshelf", "gain": -6}}]}"#;
    let by_index = r#"{"effects": [{"effect": "eq", "params": {"kind": 2, "gain": -6}}]}"#;
    assert_eq!(render(by_name, &x), render(by_index, &x));
    assert_ne!(render(by_name, &x), x);
}

#[test]
fn errors_name_what_is_wrong() {
    assert!(error(r#"{"effects": [{"effect": "flanger9"}]}"#).contains("flanger9"));
    assert!(error(r#"{"effects": [{"effect": "gain", "params": {"dbb": 1}}]}"#).contains("dbb"));
    let out = error(r#"{"effects": [{"effect": "gain", "params": {"db": 100}}]}"#);
    assert!(out.contains("db") && out.contains("100"), "{out}");
    assert!(
        error(r#"{"effects": [{"effect": "eq", "params": {"kind": "notch"}}]}"#).contains("notch")
    );
    assert!(error(r#"{"effects": [{"effect": "gain", "params": {"db": "loud"}}]}"#).contains("db"));
    assert!(error(r#"{"effects": [{"effect": "gain", "bypass": true}]}"#).contains("bypass"));
    assert!(!error("{not json").is_empty());
}

#[test]
fn the_catalogue_lists_every_effect_with_its_ranges() {
    let list: serde_json::Value = serde_json::from_str(&effects_json()).unwrap();
    let list = list.as_array().unwrap();
    let names: Vec<&str> = list.iter().map(|e| e["effect"].as_str().unwrap()).collect();
    assert_eq!(names, EFFECTS);
    let gain = &list[0]["params"][0];
    assert_eq!(gain["name"], "db");
    assert_eq!(gain["unit"], "dB");
    assert!(gain["low"].as_f64().unwrap() < 0.0 && gain["high"].as_f64().unwrap() > 0.0);
    assert_eq!(gain["default"].as_f64().unwrap(), 0.0);
    let kind = &list[3]["params"][0];
    assert_eq!(kind["name"], "kind");
    assert_eq!(kind["choices"][0], "peak");
}

#[test]
fn a_param_given_twice_is_rejected_by_name() {
    let out = error(r#"{"effects": [{"effect": "gain", "params": {"db": -3, "db": 6}}]}"#);
    assert!(out.contains("db") && out.contains("twice"), "{out}");
    let other = r#"{"effects": [{"effect": "eq", "params": {"gain": 3, "freq": 500, "gain": 3}}]}"#;
    assert!(error(other).contains("gain"));
    // distinct keys, and the same key in two slots, are fine
    let ok = r#"{"effects": [{"effect": "gain", "params": {"db": -3}},
                             {"effect": "gain", "params": {"db": 3}}]}"#;
    assert!(Rack::from_json(ok).is_ok());
}

/// A 220 Hz tone with a NaN at 0.3 s and an infinity at 0.6 s.
fn tone_with_holes() -> (Vec<[f32; 2]>, usize) {
    let mut x: Vec<[f32; 2]> = (0..48000)
        .map(|i| {
            let v = 0.4 * (i as f32 * 2.0 * std::f32::consts::PI * 220.0 / 48000.0).sin();
            [v, v]
        })
        .collect();
    x[14400] = [f32::NAN, 0.1];
    x[28800] = [0.2, f32::INFINITY];
    (x, 14400)
}

const STATEFUL: [&str; 6] = ["pitch", "octaver", "harmonizer", "tune", "unison", "eq"];

#[test]
fn a_rack_turns_non_finite_input_into_silence_instead_of_spreading_it() {
    let (x, hole) = tone_with_holes();
    for name in STATEFUL {
        let y = render(&format!(r#"{{"effects": [{{"effect": "{name}"}}]}}"#), &x);
        let bad = y.iter().flatten().filter(|v| !v.is_finite()).count();
        assert_eq!(bad, 0, "{name}: {bad} non-finite samples");
        // still sounding after the holes
        assert!(
            y[hole + 24000..].iter().any(|f| f[0].abs() > 0.05),
            "{name}"
        );
    }
}

#[test]
fn an_effect_driven_without_a_rack_also_survives_non_finite_input() {
    let (x, _) = tone_with_holes();
    for name in STATEFUL
        .iter()
        .chain(&["filter", "vibrato", "drift", "robot", "formant"])
    {
        let mut e = make(name).unwrap();
        e.reset(48000.0);
        let mut y = x.clone();
        for chunk in y.chunks_mut(100) {
            e.process(chunk);
        }
        let bad = y.iter().flatten().filter(|v| !v.is_finite()).count();
        assert_eq!(bad, 0, "{name}: {bad} non-finite samples");
    }
}

#[test]
fn a_rate_below_eight_kilohertz_is_an_error_naming_it() {
    let mut rack = Rack::from_json(r#"{"effects": [{"effect": "pitch"}]}"#).unwrap();
    for rate in [10.0, 4000.0, 7999.0, 0.0, -48000.0, f32::NAN, f32::INFINITY] {
        let err = rack.reset(rate).unwrap_err().to_string();
        assert!(err.contains(&rate.to_string()), "{err}");
    }
    assert!(rack.reset(8000.0).is_ok());
    let mut y = vec![[0.1f32, 0.2]; 4000];
    rack.process(&mut y);
}
