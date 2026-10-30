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

/// The longest the monitor may take from the mic to the ear, the I/O included.
pub const MONITOR_SECONDS: f64 = 0.010;

/// The frames a monitor rack may add to an I/O `round_trip` within [`MONITOR_SECONDS`];
/// nothing when the I/O takes it all.
pub fn monitor_budget(rate: u32, round_trip: u64) -> usize {
    let whole = (MONITOR_SECONDS * rate as f64).round() as u64;
    whole.saturating_sub(round_trip) as usize
}

/// The I/O round trip as the ports report it: the longest capture and playback latencies.
pub fn round_trip_estimate(capture: (u32, u32), playback: (u32, u32)) -> u64 {
    capture.1 as u64 + playback.1 as u64
}
