use redline_dsp_core::effect::Rack;

const PITCH_GAIN_PITCH: &str = r#"{"effects": [
    {"effect": "pitch"},
    {"effect": "gain", "params": {"db": -6}},
    {"effect": "pitch", "on": false},
    {"effect": "pitch"}
]}"#;

#[test]
fn a_slot_s_param_is_set_by_its_place_in_the_rack() {
    let mut rack = Rack::from_json(
        r#"{"effects": [{"effect": "gain"}, {"effect": "gain", "params": {"db": -6}}]}"#,
    )
    .unwrap();
    rack.reset(48000.0).unwrap();
    rack.set(0, "db", -20.0).unwrap();
    let mut block = vec![[1.0f32, 1.0]; 48000];
    rack.process(&mut block);
    // the gain glides to its new value; by the end of a second it is there
    let g = 10f32.powf(-26.0 / 20.0);
    assert!((block[47999][0] - g).abs() < 1e-4, "{}", block[47999][0]);
}

#[test]
fn a_wrong_slot_param_or_value_is_refused_naming_it() {
    let mut rack = Rack::from_json(r#"{"effects": [{"effect": "gain"}]}"#).unwrap();
    assert!(rack
        .set(1, "db", 0.0)
        .unwrap_err()
        .to_string()
        .contains('1'));
    assert!(rack
        .set(0, "dbb", 0.0)
        .unwrap_err()
        .to_string()
        .contains("dbb"));
    assert!(rack
        .set(0, "db", 99.0)
        .unwrap_err()
        .to_string()
        .contains("99"));
}

#[test]
fn the_latency_before_a_slot_sums_the_effects_on_before_it() {
    let mut rack = Rack::from_json(PITCH_GAIN_PITCH).unwrap();
    rack.reset(48000.0).unwrap();
    let total = rack.latency();
    assert!(total > 0 && total.is_multiple_of(2));
    let one = total / 2;
    assert_eq!(rack.latency_before(0), 0);
    assert_eq!(rack.latency_before(1), one);
    assert_eq!(rack.latency_before(2), one);
    // the pitch switched off adds nothing
    assert_eq!(rack.latency_before(3), one);
    assert_eq!(rack.latency_before(4), total);
    assert_eq!(rack.latency_before(99), total);
}
