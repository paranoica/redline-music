use redline_dsp_core::effect::Rack;
use redline_rec::budget::fit_latency;

/// Two limiters (lookahead 5 ms = 240 frames, then 2 ms = 96) with gains after each.
fn rack() -> Rack {
    let mut rack = Rack::from_json(
        r#"{"effects": [
            {"effect": "limiter", "params": {"lookahead": 5}},
            {"effect": "gain", "params": {"db": -1}},
            {"effect": "limiter", "params": {"lookahead": 2}},
            {"effect": "gain", "params": {"db": -2}}
        ]}"#,
    )
    .unwrap();
    rack.reset(48000.0).unwrap();
    assert_eq!(rack.latency(), 336);
    rack
}

#[test]
fn a_rack_within_the_budget_keeps_every_effect() {
    let mut r = rack();
    assert!(fit_latency(&mut r, 336).is_empty());
    assert_eq!(r.latency(), 336);
}

#[test]
fn the_last_effect_with_latency_goes_first_and_no_more_than_needed() {
    let mut r = rack();
    assert_eq!(fit_latency(&mut r, 335), ["limiter"]);
    // the 2 ms limiter went: the 5 ms one is left
    assert_eq!(r.latency(), 240);
    let mut r = rack();
    assert_eq!(fit_latency(&mut r, 240), ["limiter"]);
    assert_eq!(r.latency(), 240);
}

#[test]
fn effects_go_from_the_end_until_the_rack_fits_and_ones_without_latency_stay() {
    let mut r = rack();
    assert_eq!(fit_latency(&mut r, 239), ["limiter", "limiter"]);
    assert_eq!(r.latency(), 0);
    // the gains still play: -3 dB in all
    let mut block = [[1.0f32, 1.0]; 8];
    r.process(&mut block);
    let g = 10f32.powf(-3.0 / 20.0);
    assert!((block[7][0] - g).abs() < 1e-5, "{}", block[7][0]);
}

#[test]
fn a_rack_without_latency_fits_a_budget_of_zero() {
    let mut r = Rack::from_json(r#"{"effects": [{"effect": "gain"}]}"#).unwrap();
    r.reset(48000.0).unwrap();
    assert!(fit_latency(&mut r, 0).is_empty());
}
