// Times the beat player on a worst-case score in 128-frame blocks against the block's real
// time: cargo run --release -p redline-dsp-core --example beat_timing
use std::time::Instant;

use redline_dsp_core::beat::player::Player;
use redline_dsp_core::beat::score::Score;
use redline_dsp_core::beat::voices::SampleBank;
use serde_json::{json, Value};

const RATE: f32 = 48000.0;
const BLOCK: usize = 128;
const SECONDS: f64 = 20.0;

fn note(midi: u8) -> String {
    const NAMES: [&str; 12] = [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ];
    format!("{}{}", NAMES[midi as usize % 12], midi as i32 / 12 - 1)
}

/// Four-note chords held through each bar, so with the releases about eight voices ring.
fn chords(root: u8) -> Value {
    let mut notes = Vec::new();
    for (bar, shift) in [0u8, 5, 3, 7].iter().enumerate() {
        for interval in [0u8, 3, 7, 10] {
            notes.push(json!({
                "step": bar * 16, "len": 16, "note": note(root + shift + interval), "vel": 0.7
            }));
        }
    }
    Value::Array(notes)
}

fn rack(threshold: f64) -> Value {
    json!([
        {"effect": "compressor", "params": {"threshold": threshold, "ratio": 4}},
        {"effect": "saturate", "params": {"drive": 9}},
        {"effect": "eq", "params": {"freq": 3000, "gain": 2}}
    ])
}

fn main() {
    let saw = json!({"instrument": "saw", "params": {"voices": 9, "release": 0.8}});
    let fm = json!({"instrument": "fm", "params": {"preset": "keys", "release": 0.8}});
    let pluck = json!({"instrument": "pluck", "params": {"release": 0.8}});
    let score = json!({
        "version": 1, "bpm": 140, "swing": 56, "seed": 3, "key": "C minor",
        "sounds": {
            "kick": {"instrument": "kick"},
            "kick2": {"instrument": "kick", "params": {"tune": 45}},
            "snare": {"instrument": "snare"},
            "clap": {"instrument": "clap"},
            "hat": {"instrument": "hat_closed"},
            "open": {"instrument": "hat_open"},
            "rim": {"instrument": "rim"},
            "bell": {"instrument": "cowbell"},
            "bass": {"instrument": "bass808"},
            "saw_a": saw.clone(),
            "saw_b": saw,
            "fm_a": fm.clone(),
            "fm_b": fm,
            "pluck_a": pluck.clone(),
            "pluck_b": pluck,
            "keys": {"instrument": "sampler"}
        },
        "patterns": {"p": {"bars": 4, "rows": {
            "kick": "x..x..x...x..x..".repeat(4),
            "kick2": "......x.......x.".repeat(4),
            "snare": "....x.......x..g".repeat(4),
            "clap": "....x.......x...".repeat(4),
            "hat": "x3x4x6xgx2x3x4x6".repeat(4),
            "open": "..x...x...x...x.".repeat(4),
            "rim": ".x.x.x.x.x.x.x.x".repeat(4),
            "bell": "x.x.x.x.x.x.x.x.".repeat(4),
            "bass": [
                {"step": 0, "len": 10, "note": note(36), "vel": 1.0},
                {"step": 8, "len": 12, "note": note(43), "vel": 1.0},
                {"step": 32, "len": 20, "note": note(39), "vel": 1.0},
                {"step": 48, "len": 16, "note": note(41), "vel": 1.0}
            ],
            "saw_a": chords(48),
            "saw_b": chords(60),
            "fm_a": chords(55),
            "fm_b": chords(67),
            "pluck_a": chords(60),
            "pluck_b": chords(72)
        }}},
        "sections": [{"name": "a", "bars": 16, "play": ["p"]}],
        "mix": {
            "sounds": {
                "kick": {"rack": rack(-20.0)},
                "snare": {"rack": rack(-20.0)},
                "hat": {"rack": rack(-24.0)},
                "bass": {"rack": rack(-18.0), "sidechain": "kick"},
                "saw_a": {"rack": rack(-24.0)},
                "saw_b": {"rack": rack(-24.0)},
                "fm_a": {"rack": rack(-24.0)},
                "pluck_a": {"rack": rack(-24.0)}
            },
            "master": [
                {"effect": "compressor", "params": {"threshold": -12}},
                {"effect": "limiter"}
            ]
        }
    });
    let score = Score::from_value(&score).unwrap_or_else(|e| panic!("{e:?}"));
    let mut player = Player::new(&score, RATE, SampleBank::default()).expect("player");
    let blocks = (SECONDS * RATE as f64) as usize / BLOCK;
    let mut out = vec![[0.0f32; 2]; BLOCK];
    let mut times = Vec::with_capacity(blocks);
    let mut peak = 0.0f32;
    let mut voices = 0;
    for _ in 0..blocks {
        let t = Instant::now();
        player.process(&mut out);
        times.push(t.elapsed().as_secs_f64() * 1e6);
        voices = voices.max(player.voices());
        peak = out
            .iter()
            .fold(peak, |p, f| p.max(f[0].abs()).max(f[1].abs()));
    }
    let budget = BLOCK as f64 / RATE as f64 * 1e6;
    let mean = times.iter().sum::<f64>() / times.len() as f64;
    let mut sorted = times.clone();
    sorted.sort_by(f64::total_cmp);
    let p99 = sorted[sorted.len() * 99 / 100];
    let max = sorted[sorted.len() - 1];
    println!(
        "{} sounds, {blocks} blocks of {BLOCK}: mean {mean:.1} us, p99 {p99:.1} us, max {max:.1} us \
         of {budget:.0} us ({:.0}% mean); up to {voices} voices, peak {peak:.2}",
        score.sounds.len(),
        100.0 * mean / budget
    );
}
