//! The monitor's latency budget.

use redline_dsp_core::effect::Rack;

/// Switches effects off from the end of a built and reset rack until its latency fits
/// `budget` frames; the names of those switched off, last first. An effect without latency
/// stays: switching it off would not help.
pub fn fit_latency(rack: &mut Rack, budget: usize) -> Vec<&'static str> {
    let mut dropped = Vec::new();
    for slot in (0..rack.len()).rev() {
        if rack.latency() <= budget {
            break;
        }
        // zero for an effect already off
        if rack.latency_before(slot + 1) == rack.latency_before(slot) {
            continue;
        }
        rack.turn_off(slot).expect("the slot is in the rack");
        dropped.push(rack.effect_name(slot).expect("the slot is in the rack"));
    }
    dropped
}
