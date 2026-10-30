use redline_dsp_core::effect::{effects_json, Rack, EFFECTS};

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
    rack.reset(48000.0);
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
