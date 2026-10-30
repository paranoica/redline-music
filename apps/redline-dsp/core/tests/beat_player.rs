use redline_dsp_core::beat::player::{render, Player};
use redline_dsp_core::beat::score::Score;
use redline_dsp_core::beat::voices::SampleBank;
use redline_dsp_core::effect::Rack;
use serde_json::{json, Value};
use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

struct Counting;

thread_local! {
    static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
}

fn count() {
    let _ = ALLOCATIONS.try_with(|c| c.set(c.get() + 1));
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc(layout) }
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc_zeroed(layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        count();
        unsafe { System.realloc(ptr, layout, new_size) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        count();
        unsafe { System.dealloc(ptr, layout) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

fn allocations(f: impl FnOnce()) -> usize {
    let before = ALLOCATIONS.with(Cell::get);
    f();
    ALLOCATIONS.with(Cell::get) - before
}

const RATE: f32 = 48000.0;
/// A bar at 120 bpm and 48 kHz.
const BAR: u64 = 96000;
/// A 16th.
const STEP: u64 = 6000;

fn score(sounds: Value, patterns: Value, sections: Value, mix: Value) -> Score {
    let v = json!({
        "version": 1, "bpm": 120, "swing": 50, "seed": 5, "key": "C minor",
        "sounds": sounds, "patterns": patterns, "sections": sections, "mix": mix
    });
    Score::from_value(&v).unwrap_or_else(|e| panic!("{e:?}"))
}

/// A MIDI note by name, as a score writes it.
fn n(midi: u8) -> String {
    const NAMES: [&str; 12] = [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ];
    format!("{}{}", NAMES[midi as usize % 12], midi as i32 / 12 - 1)
}

fn bank() -> SampleBank {
    SampleBank::default()
}

fn player(s: &Score) -> Player {
    Player::new(s, RATE, bank()).unwrap()
}

/// `frames` of the player in blocks of `block`, the mix and a stem a sound.
fn play(
    p: &mut Player,
    sounds: usize,
    frames: usize,
    block: usize,
) -> (Vec<[f32; 2]>, Vec<Vec<[f32; 2]>>) {
    let mut mix = Vec::with_capacity(frames);
    let mut stems = vec![Vec::with_capacity(frames); sounds];
    let mut out = vec![[0.0f32; 2]; block];
    let mut parts = vec![vec![[0.0f32; 2]; block]; sounds];
    let mut done = 0;
    while done < frames {
        let n = block.min(frames - done);
        p.process_stems(&mut out[..n], &mut parts);
        mix.extend_from_slice(&out[..n]);
        for (s, part) in stems.iter_mut().zip(&parts) {
            s.extend_from_slice(&part[..n]);
        }
        done += n;
    }
    (mix, stems)
}

fn loud(f: &[f32; 2]) -> bool {
    f[0] != 0.0 || f[1] != 0.0
}

/// The first frame in `from..to` that is not silent.
fn onset(x: &[[f32; 2]], from: u64, to: u64) -> Option<u64> {
    (from..to.min(x.len() as u64)).find(|&i| loud(&x[i as usize]))
}

fn bits(x: &[[f32; 2]]) -> Vec<[u32; 2]> {
    x.iter().map(|f| [f[0].to_bits(), f[1].to_bits()]).collect()
}

fn rms(x: &[[f32; 2]]) -> f64 {
    (x.iter().map(|f| (f[0] as f64).powi(2)).sum::<f64>() / x.len() as f64).sqrt()
}

fn kick_and_clap(kick_rack: Value) -> Score {
    score(
        json!({"kick": {"instrument": "kick"}, "clap": {"instrument": "clap"}}),
        json!({"p": {"bars": 1, "rows": {
            "kick": "x...............",
            "clap": "x..............."
        }}}),
        json!([{"name": "a", "bars": 4, "play": ["p"]}]),
        json!({"sounds": {"kick": {"rack": kick_rack}}}),
    )
}

#[test]
fn a_kick_on_each_bar_starts_on_the_bar() {
    let s = score(
        json!({"kick": {"instrument": "kick"}}),
        json!({"p": {"bars": 1, "rows": {"kick": "x..............."}}}),
        json!([{"name": "a", "bars": 4, "play": ["p"]}]),
        json!({}),
    );
    let r = render(&s, RATE, bank()).unwrap();
    assert_eq!(r.length, 4 * BAR);
    for bar in 0..4 {
        let from = (bar * BAR).saturating_sub(BAR / 2);
        assert_eq!(
            onset(&r.mix, from, (bar + 1) * BAR),
            Some(bar * BAR),
            "bar {bar}"
        );
    }
}

#[test]
fn a_late_rack_on_one_sound_delays_the_others_to_match() {
    // a 4 ms lookahead: 192 frames late
    let s = kick_and_clap(json!([{"effect": "limiter", "params": {"lookahead": 4}}]));
    let p = player(&s);
    assert_eq!(p.latency(), 192);
    assert_eq!(p.stem_latency(), 192);
    let r = render(&s, RATE, bank()).unwrap();
    let (clap, kick) = (s.sound("clap").unwrap(), s.sound("kick").unwrap());
    for bar in 0..4 {
        let from = (bar * BAR).saturating_sub(BAR / 2);
        let to = (bar + 1) * BAR;
        assert_eq!(
            onset(&r.stems[kick], from, to),
            Some(bar * BAR),
            "kick bar {bar}"
        );
        assert_eq!(
            onset(&r.stems[clap], from, to),
            Some(bar * BAR),
            "clap bar {bar}"
        );
        assert_eq!(onset(&r.mix, from, to), Some(bar * BAR), "mix bar {bar}");
    }
}

#[test]
fn the_master_rack_latency_is_trimmed_from_the_render() {
    let mut s = kick_and_clap(json!([]));
    s.mix.master =
        json!({"effects": [{"effect": "limiter", "params": {"lookahead": 4}}]}).to_string();
    let p = player(&s);
    assert_eq!((p.latency(), p.stem_latency()), (192, 0));
    let r = render(&s, RATE, bank()).unwrap();
    for bar in 0..4 {
        let from = (bar * BAR).saturating_sub(BAR / 2);
        assert_eq!(
            onset(&r.mix, from, (bar + 1) * BAR),
            Some(bar * BAR),
            "bar {bar}"
        );
    }
}

/// Drums with rolls and ghosts, an 808 gliding, chords on fm and pluck, a sampler-free mix
/// through stateful but block-free racks.
fn busy() -> Score {
    score(
        json!({
            "kick": {"instrument": "kick"},
            "hat": {"instrument": "hat_closed"},
            "open": {"instrument": "hat_open"},
            "snare": {"instrument": "snare"},
            "bass": {"instrument": "bass808", "params": {"glide_ms": 200}},
            "keys": {"instrument": "fm", "params": {"preset": "keys"}},
            "pluck": {"instrument": "pluck"},
            "pad": {"instrument": "saw"}
        }),
        json!({"p": {"bars": 2, "rows": {
            "kick":  "x.....x...x.....|x..x......x..x..",
            "hat":   "x3x.x4x.x6xgx2x.|xxxxxxxx4xxxxx6x",
            "open":  "..x.......x.....|..x.......x.....",
            "snare": "....x.......x...|....x.......x.g.",
            "bass": [
                {"step": 0, "len": 8, "note": n(36), "vel": 1.0},
                {"step": 6, "len": 6, "note": n(43), "vel": 0.8},
                {"step": 16, "len": 16, "note": n(39), "vel": 0.9}
            ],
            "keys": [
                {"step": 0, "len": 12, "note": n(60), "vel": 0.7},
                {"step": 0, "len": 12, "note": n(63), "vel": 0.7},
                {"step": 0, "len": 12, "note": n(67), "vel": 0.7},
                {"step": 2, "len": 8, "note": n(70), "vel": 0.5},
                {"step": 4, "len": 8, "note": n(72), "vel": 0.5},
                {"step": 16, "len": 16, "note": n(58), "vel": 0.6},
                {"step": 16, "len": 16, "note": n(62), "vel": 0.6}
            ],
            "pluck": [
                {"step": 3, "len": 2, "note": n(72), "vel": 0.9},
                {"step": 7, "len": 20, "note": n(75), "vel": 0.9},
                {"step": 11, "len": 2, "note": n(79), "vel": 0.9}
            ],
            "pad": [
                {"step": 0, "len": 32, "note": n(48), "vel": 0.4},
                {"step": 0, "len": 32, "note": n(55), "vel": 0.4}
            ]
        }}}),
        json!([{"name": "a", "bars": 4, "play": ["p"]}]),
        json!({
            "sounds": {
                "kick": {"gain_db": -3, "rack": [{"effect": "saturate", "params": {"drive": 12}}]},
                "hat": {"pan": -0.4, "rack": [{"effect": "gain", "params": {"db": -2}}]},
                "bass": {"rack": [{"effect": "saturate", "params": {"drive": 6, "mix": 0.5}}]},
                "keys": {"pan": 0.3}
            },
            "master": [{"effect": "gain", "params": {"db": -1}}]
        }),
    )
}

#[test]
fn the_same_score_renders_bit_identical_twice() {
    let s = busy();
    let a = render(&s, RATE, bank()).unwrap();
    let b = render(&s, RATE, bank()).unwrap();
    assert!(a.mix.iter().any(loud));
    assert_eq!(bits(&a.mix), bits(&b.mix));
    for (x, y) in a.stems.iter().zip(&b.stems) {
        assert_eq!(bits(x), bits(y));
    }
}

#[test]
fn block_sizes_do_not_change_the_samples() {
    let s = busy();
    let n = (2 * BAR + BAR / 3) as usize;
    let (want, want_stems) = play(&mut player(&s), s.sounds.len(), n, 4096);
    assert!(want.iter().any(loud));
    for block in [1, 64, 128, 4096 * 3 + 5] {
        let (got, stems) = play(&mut player(&s), s.sounds.len(), n, block);
        assert_eq!(bits(&got), bits(&want), "block {block}");
        for (k, (g, w)) in stems.iter().zip(&want_stems).enumerate() {
            assert_eq!(bits(g), bits(w), "block {block}, sound {k}");
        }
    }
    // plain process gives the same mix as process_stems
    let mut p = player(&s);
    let mut out = vec![[0.0f32; 2]; n];
    p.process(&mut out);
    assert_eq!(bits(&out), bits(&want));
}

/// Crossing bar 4: an 808 note gliding into one across it, an open hat ringing over it
/// (choked after), a ninth fm note stealing a voice just before it and a long pluck.
fn crossing() -> Score {
    let mut keys: Vec<Value> = (0..8)
        .map(|i| json!({"step": 48 + i, "len": 20, "note": n(60 + 2 * i), "vel": 0.6}))
        .collect();
    keys.push(json!({"step": 63, "len": 6, "note": n(79), "vel": 0.8}));
    score(
        json!({
            "bass": {"instrument": "bass808", "params": {"glide_ms": 500, "decay": 8}},
            "open": {"instrument": "hat_open", "params": {"decay": 2.0}},
            "closed": {"instrument": "hat_closed"},
            "keys": {"instrument": "fm", "params": {"preset": "keys", "decay": 8}},
            "pluck": {"instrument": "pluck", "params": {"decay": 10}}
        }),
        json!({"p": {"bars": 8, "rows": {
            "bass": [
                {"step": 56, "len": 10, "note": n(31), "vel": 1.0},
                {"step": 63, "len": 8, "note": n(38), "vel": 0.9}
            ],
            "open": format!("{}x{}", ".".repeat(62), ".".repeat(65)),
            "closed": format!("{}x{}", ".".repeat(70), ".".repeat(57)),
            "keys": keys,
            "pluck": [{"step": 40, "len": 40, "note": n(55), "vel": 1.0}]
        }}}),
        json!([{"name": "a", "bars": 8, "play": ["p"]}]),
        json!({}),
    )
}

#[test]
fn seek_plays_on_as_the_whole_render_does() {
    let s = crossing();
    let full = render(&s, RATE, bank()).unwrap();
    let k = s.sounds.len();
    // the bar line, inside the stolen voice's fade, and inside the glide
    for frame in [4 * BAR, 63 * STEP + 100, 63 * STEP + 3000] {
        let mut p = player(&s);
        // something already ringing in the racks or voices must not matter
        play(&mut p, k, 5000, 128);
        p.seek(frame);
        assert_eq!(p.position(), frame);
        let n = BAR as usize;
        let (mix, stems) = play(&mut p, k, n, 128);
        let at = frame as usize;
        for (sound, (got, want)) in stems.iter().zip(&full.stems).enumerate() {
            let want = &want[at..at + n];
            assert!(
                bits(got) == bits(want),
                "{} differs after seek({frame})",
                s.sounds[sound].name
            );
        }
        assert_eq!(bits(&mix), bits(&full.mix[at..at + n]));
        assert!(mix[..100].iter().any(loud));
    }
}

#[test]
fn the_crossing_score_has_what_the_seek_test_needs() {
    use redline_dsp_core::beat::events::EventTable;
    let s = crossing();
    let t = EventTable::build(&s, RATE);
    let of = |name: &str| {
        let k = s.sound(name).unwrap() as u32;
        t.events.iter().filter(move |e| e.sound == k)
    };
    let bar4 = 4 * BAR;
    assert!(of("bass").any(|e| e.legato_from.is_some() && e.start < bar4 && e.off.unwrap() > bar4));
    assert!(of("open").any(|e| e.start < bar4 && e.end.unwrap() > bar4));
    // the first keys note is stolen by the ninth
    assert!(of("keys").any(|e| e.end == Some(63 * STEP)));
    assert!(of("pluck").any(|e| e.start < bar4 && e.off.unwrap() > bar4));
}

fn swap_scores(hat: &str) -> Score {
    score(
        json!({
            "hat": {"instrument": "hat_closed"},
            "keys": {"instrument": "fm", "params": {"preset": "keys", "decay": 8}}
        }),
        json!({"p": {"bars": 2, "rows": {
            "hat": hat,
            "keys": [{"step": 0, "len": 32, "note": n(60), "vel": 0.8}]
        }}}),
        json!([{"name": "a", "bars": 4, "play": ["p"]}]),
        json!({}),
    )
}

#[test]
fn set_score_keeps_ringing_voices_and_plays_the_new_events() {
    let old = swap_scores("x...............|x...............");
    let new = swap_scores("........x.......|........x.......");
    let hat = old.sound("hat").unwrap();
    let keys = old.sound("keys").unwrap();
    let k = old.sounds.len();
    let swap = (BAR + 2 * STEP + 77) as usize;
    let n = 3 * BAR as usize - swap;

    let (_, whole) = play(&mut player(&old), k, swap + n, 128);
    let mut p = player(&old);
    let (_, before) = play(&mut p, k, swap, 128);
    p.set_score(&new).unwrap();
    let (_, after) = play(&mut p, k, n, 128);

    // the note that began under the old score rings on exactly as it would have
    assert_eq!(bits(&before[keys]), bits(&whole[keys][..swap]));
    assert_eq!(bits(&after[keys]), bits(&whole[keys][swap..]));
    let jump = (after[keys][0][0] - before[keys][swap - 1][0]).abs();
    assert!(jump < 0.05, "{jump}");
    assert!(after[keys][..100].iter().any(loud));

    // bar 2 follows the new table: a hit on step 8, none on step 0
    let at = |f: u64| f - swap as u64;
    assert_eq!(
        onset(&after[hat], at(2 * BAR - STEP), at(3 * BAR)),
        Some(at(2 * BAR + 8 * STEP))
    );
    // and bar 1's step 8, after the swap, is the new one too
    assert_eq!(
        onset(&after[hat], 0, at(2 * BAR - STEP)),
        Some(at(BAR + 8 * STEP))
    );
}

#[test]
fn set_score_refuses_other_sounds() {
    let s = swap_scores("x...............|x...............");
    let mut p = player(&s);
    let renamed = score(
        json!({
            "hats": {"instrument": "hat_closed"},
            "keys": {"instrument": "fm"}
        }),
        json!({"p": {"bars": 1, "rows": {"hats": "x..............."}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({}),
    );
    let err = p.set_score(&renamed).unwrap_err();
    assert!(err.starts_with("rebuild"), "{err}");
    let other_instrument = score(
        json!({
            "hat": {"instrument": "hat_open"},
            "keys": {"instrument": "fm"}
        }),
        json!({"p": {"bars": 1, "rows": {"hat": "x..............."}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({}),
    );
    assert!(p
        .set_score(&other_instrument)
        .unwrap_err()
        .starts_with("rebuild"));
    // the player still plays its old score
    let (mix, _) = play(&mut p, 2, 1000, 128);
    assert!(mix.iter().any(loud));
}

fn reset_rack(effects: Value) -> Rack {
    let mut r = Rack::from_json(&json!({ "effects": effects }).to_string()).unwrap();
    r.reset(RATE).unwrap();
    r
}

#[test]
fn set_rack_crossfades_over_10_ms_and_hands_back_the_retired_rack() {
    let s = score(
        json!({"pad": {"instrument": "fm", "params": {"preset": "keys", "index": 0, "decay": 8}}}),
        json!({"p": {"bars": 1, "rows": {"pad": [{"step": 0, "len": 16, "note": n(57), "vel": 1.0}]}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({"sounds": {"pad": {"rack": [
            {"effect": "gain", "params": {"db": 0}},
            {"effect": "gain", "params": {"db": 0}}
        ]}}}),
    );
    let swap = 24000;
    let n = 4800;
    let (dry, _) = play(&mut player(&s), 1, swap + n, 128);

    let mut p = player(&s);
    let (before, _) = play(&mut p, 1, swap, 128);
    let retired = p.set_rack(
        0,
        reset_rack(json!([{"effect": "gain", "params": {"db": -12}}])),
    );
    assert!(retired.unwrap().is_none());
    let (after, _) = play(&mut p, 1, n, 128);
    assert_eq!(bits(&before), bits(&dry[..swap]));

    let quarter = 10f32.powf(-12.0 / 20.0);
    let fade = 480;
    let ratio = |i: usize| after[i][0] / dry[swap + i][0];
    let mut prev = 1.0f32;
    for i in 0..fade + 200 {
        if dry[swap + i][0].abs() < 1e-3 {
            continue;
        }
        let r = ratio(i);
        // linear from the old gain to the new, never a step
        let want = if i < fade {
            quarter + (1.0 - quarter) * (fade - i) as f32 / fade as f32
        } else {
            quarter
        };
        assert!((r - want).abs() < 1e-3, "frame {i}: {r} against {want}");
        assert!(
            (prev - r).abs() < 2.0 * (1.0 - quarter) / fade as f32 + 1e-3,
            "frame {i}"
        );
        prev = r;
    }

    // the next swap hands back the score's own rack, the one the first faded out
    let retired = p.set_rack(0, reset_rack(json!([]))).unwrap().unwrap();
    assert_eq!(retired.len(), 2);
    // a rack later than the player compensates is refused
    let late = reset_rack(json!([{"effect": "limiter"}]));
    assert!(p.set_rack(0, late).err().unwrap().starts_with("rebuild"));
    assert!(p.set_rack(5, reset_rack(json!([]))).is_err());
}

#[test]
fn a_swap_during_a_fade_hands_back_only_racks_no_longer_heard() {
    let s = kick_and_clap(json!([{"effect": "gain"}]));
    let kick = s.sound("kick").unwrap();
    let mut p = player(&s);
    play(&mut p, 2, 1000, 128);
    let two = reset_rack(json!([{"effect": "gain"}, {"effect": "gain"}]));
    assert!(p.set_rack(kick, two).unwrap().is_none());
    // a quarter into the fade both racks are heard: both fade on and nothing comes back
    play(&mut p, 2, 120, 120);
    assert!(p.set_rack(kick, reset_rack(json!([]))).unwrap().is_none());
    // three quarters into that fade three racks are heard: the quietest, the two-gain
    // rack, is the one retired
    play(&mut p, 2, 360, 120);
    let back = p
        .set_rack(kick, reset_rack(json!([{"effect": "pan"}])))
        .unwrap()
        .unwrap();
    assert_eq!(back.len(), 2);
    // once the fade is over, the silent racks come back the earliest first
    play(&mut p, 2, 600, 120);
    let back = p.set_rack(kick, reset_rack(json!([]))).unwrap().unwrap();
    assert_eq!(back.len(), 1);
    assert_eq!(back.effect_name(0), Some("gain"));
    play(&mut p, 2, 600, 120);
    let back = p.set_rack(kick, reset_rack(json!([]))).unwrap().unwrap();
    assert_eq!(back.len(), 0);
}

fn ducked(sidechain: bool) -> Vec<[f32; 2]> {
    let mut bass = json!({"rack": [{"effect": "compressor", "params": {
        "threshold": -40, "ratio": 20, "attack": 0.1, "release": 50, "knee": 0,
        "detector": "side"
    }}]});
    if sidechain {
        bass["sidechain"] = json!("kick");
    }
    let s = score(
        json!({
            "kick": {"instrument": "kick"},
            "bass": {"instrument": "bass808", "params": {"decay": 8}}
        }),
        json!({"p": {"bars": 1, "rows": {
            "kick": "........x.......",
            "bass": [{"step": 2, "len": 14, "note": n(33), "vel": 1.0}]
        }}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({"sounds": {"bass": bass}}),
    );
    render(&s, RATE, bank()).unwrap().stems[s.sound("bass").unwrap()].clone()
}

#[test]
fn a_sidechained_kick_ducks_the_808() {
    let plain = ducked(false);
    let ducked = ducked(true);
    let span = |x: &[[f32; 2]], from: u64, to: u64| rms(&x[from as usize..to as usize]);
    // before the kick both are the same
    let a = span(&plain, 4 * STEP, 8 * STEP);
    let b = span(&ducked, 4 * STEP, 8 * STEP);
    assert!(a > 1e-3 && (a - b).abs() / a < 1e-4, "{a} {b}");
    // on the kick the 808 goes far down
    let kick = 8 * STEP;
    let a = span(&plain, kick + 480, kick + 4800);
    let b = span(&ducked, kick + 480, kick + 4800);
    assert!(20.0 * (b / a).log10() < -10.0, "{a} {b}");
}

#[test]
fn process_and_seek_allocate_nothing() {
    let mut s = busy();
    let bass = s.sound("bass").unwrap();
    s.mix.sounds[bass].rack = json!({"effects": [
        {"effect": "compressor", "params": {"detector": "side"}},
        {"effect": "limiter"}
    ]})
    .to_string();
    s.mix.sounds[bass].sidechain = s.sound("kick");
    s.mix.master = json!({"effects": [{"effect": "limiter"}]}).to_string();
    let mut p = player(&s);
    let k = s.sounds.len();
    let mut out = vec![[0.0f32; 2]; 4096 * 2 + 17];
    let mut stems = vec![vec![[0.0f32; 2]; out.len()]; k];
    let fresh = reset_rack(json!([{"effect": "gain", "params": {"db": -6}}]));
    let mut kept = None;
    let n = allocations(|| {
        for _ in 0..8 {
            p.process(&mut out[..128]);
        }
        p.process(&mut out);
        p.process_stems(&mut out, &mut stems);
        p.seek(BAR + 3 * STEP + 11);
        p.process(&mut out[..1000]);
        kept = Some(p.set_rack(s.sound("keys").unwrap(), fresh));
        for _ in 0..40 {
            p.process(&mut out[..128]);
        }
        p.seek(0);
        p.process(&mut out);
    });
    assert_eq!(n, 0);
    assert!(out.iter().any(loud));
    drop(kept);
}

#[test]
fn an_all_rest_score_renders_silence_of_the_songs_length() {
    let s = score(
        json!({"kick": {"instrument": "kick"}, "keys": {"instrument": "fm"}}),
        json!({
            "rest": {"bars": 1, "rows": {"kick": "................", "keys": []}},
            "none": {"bars": 1, "rows": {}}
        }),
        json!([
            {"name": "a", "bars": 2, "play": ["rest"]},
            {"name": "b", "bars": 1, "play": ["none"]}
        ]),
        json!({}),
    );
    let r = render(&s, RATE, bank()).unwrap();
    assert_eq!(r.length, 3 * BAR);
    assert_eq!(r.mix.len() as u64, 3 * BAR);
    assert!(r.stems.iter().all(|x| x.len() as u64 == 3 * BAR));
    assert!(!r.mix.iter().any(loud));
}

#[test]
fn the_last_step_of_the_last_bar_plays_with_its_tail() {
    let s = score(
        json!({"hat": {"instrument": "hat_open", "params": {"decay": 0.5}}}),
        json!({"p": {"bars": 1, "rows": {"hat": "...............x"}}}),
        json!([
            {"name": "a", "bars": 4, "play": ["p"]},
            {"name": "b", "bars": 2, "play": ["p"]}
        ]),
        json!({}),
    );
    let r = render(&s, RATE, bank()).unwrap();
    let last = 5 * BAR + 15 * STEP;
    assert_eq!(r.length, 6 * BAR);
    assert_eq!(onset(&r.mix, last - STEP, 7 * BAR), Some(last));
    // the hit rings past the song's end and the render keeps it
    assert!(r.mix.len() as u64 > 6 * BAR + 4800);
    assert!(r.mix[(6 * BAR) as usize..].iter().any(loud));
    // and stops once it is silent: no long quiet tail
    let end = r
        .mix
        .iter()
        .rposition(|f| f[0].abs() >= 1e-6 || f[1].abs() >= 1e-6)
        .unwrap();
    assert_eq!(end + 1, r.mix.len());
}

fn sampler_score() -> Score {
    score(
        json!({"keys": {"instrument": "sampler", "library": "sines"}}),
        json!({"p": {"bars": 1, "rows": {"keys": [{"step": 0, "len": 8, "note": n(60), "vel": 1.0}]}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({}),
    )
}

fn sine() -> Vec<i16> {
    (0..48000)
        .map(|i| ((i as f64 * 261.63 / 48000.0 * std::f64::consts::TAU).sin() * 16000.0) as i16)
        .collect()
}

#[test]
fn a_sampler_plays_a_zone_loaded_into_the_players_bank() {
    let s = sampler_score();
    let mut p = player(&s);
    p.bank_mut()
        .load("sines", 60, 0..=127, 48000.0, &sine(), 1)
        .unwrap();
    p.rebuild_samplers().unwrap();
    let (mix, _) = play(&mut p, 1, 24000, 128);
    assert!(rms(&mix[1000..20000]) > 0.1, "{}", rms(&mix));

    // and through render, with the bank loaded before
    let mut b = bank();
    b.load("sines", 60, 0..=127, 48000.0, &sine(), 1).unwrap();
    let r = render(&s, RATE, b).unwrap();
    assert!(rms(&r.mix[1000..20000]) > 0.1);
}

#[test]
fn a_sampler_without_its_library_is_silent() {
    let s = sampler_score();
    let mut p = player(&s);
    let (mix, _) = play(&mut p, 1, 48000, 128);
    assert!(!mix.iter().any(loud));
    // another library loaded does not stand in for it
    p.bank_mut()
        .load("other", 60, 0..=127, 48000.0, &sine(), 1)
        .unwrap();
    p.rebuild_samplers().unwrap();
    p.seek(0);
    let (mix, _) = play(&mut p, 1, 48000, 128);
    assert!(!mix.iter().any(loud));
    let r = render(&s, RATE, bank()).unwrap();
    assert_eq!(r.mix.len() as u64, BAR);
    assert!(!r.mix.iter().any(loud));
}

#[test]
fn a_rate_the_racks_refuse_is_an_error() {
    let s = sampler_score();
    assert!(Player::new(&s, 4000.0, bank()).is_err());
    assert!(Player::new(&s, f32::NAN, bank()).is_err());
}

#[test]
fn gain_and_pan_follow_the_equal_power_law_with_unity_in_the_centre() {
    let with = |mix: Value| {
        let s = score(
            json!({"kick": {"instrument": "kick"}}),
            json!({"p": {"bars": 1, "rows": {"kick": "x..............."}}}),
            json!([{"name": "a", "bars": 1, "play": ["p"]}]),
            json!({"sounds": {"kick": mix}}),
        );
        render(&s, RATE, bank()).unwrap().mix
    };
    let centre = with(json!({}));
    // unity in the centre: the mix is the voice itself
    {
        use redline_dsp_core::beat::events::EventTable;
        use redline_dsp_core::beat::voices::{make, Ctx};
        let s = score(
            json!({"kick": {"instrument": "kick"}}),
            json!({"p": {"bars": 1, "rows": {"kick": "x..............."}}}),
            json!([{"name": "a", "bars": 1, "play": ["p"]}]),
            json!({}),
        );
        let t = EventTable::build(&s, RATE);
        let b = bank();
        let ctx = Ctx {
            rate: RATE,
            bank: &b,
        };
        let mut v = make("kick", RATE).unwrap();
        v.start(&t.events[0], &s.sounds[0].params, &ctx);
        let mut alone = vec![[0.0f32; 2]; 24000];
        v.render(&mut alone, &ctx);
        for (a, c) in alone.iter().zip(&centre) {
            assert!(
                (a[0] - c[0]).abs() <= 1e-6 * a[0].abs().max(1e-3),
                "{a:?} {c:?}"
            );
        }
    }
    let right = with(json!({"gain_db": -6, "pan": 1}));
    let half = with(json!({"pan": -0.5}));
    let g = 10f64.powf(-6.0 / 20.0) * std::f64::consts::SQRT_2;
    let (l, r) = (
        (3.0 * std::f64::consts::PI / 8.0).cos() * std::f64::consts::SQRT_2,
        (std::f64::consts::PI / 8.0).cos() * std::f64::consts::SQRT_2,
    );
    let mut checked = 0;
    for i in 0..centre.len() {
        let c = centre[i][0] as f64;
        assert_eq!(centre[i][0], centre[i][1]);
        if c.abs() < 1e-3 {
            continue;
        }
        assert!(right[i][0].abs() < 1e-6, "frame {i}");
        assert!((right[i][1] as f64 / c - g).abs() < 1e-5, "frame {i}");
        assert!((half[i][0] as f64 / c - r).abs() < 1e-5, "frame {i}");
        assert!((half[i][1] as f64 / c - l).abs() < 1e-5, "frame {i}");
        checked += 1;
    }
    assert!(checked > 1000);
}

/// Four-note saw chords every bar with a 3 s release, so twelve voices ring at once, and a
/// mono 808 with three notes on one step.
fn piled_releases() -> Score {
    let chords: Vec<Value> = (0..3u8)
        .flat_map(|bar| {
            [0u8, 3, 7, 10].map(|i| {
                json!({"step": bar as u32 * 16, "len": 16, "note": n(48 + 2 * bar + i), "vel": 0.6})
            })
        })
        .collect();
    score(
        json!({
            "pad": {"instrument": "saw", "params": {"voices": 3, "release": 3.0}},
            "bass": {"instrument": "bass808"}
        }),
        json!({"p": {"bars": 4, "rows": {
            "pad": chords,
            "bass": [
                {"step": 4, "len": 4, "note": n(36), "vel": 1.0},
                {"step": 4, "len": 4, "note": n(40), "vel": 1.0},
                {"step": 4, "len": 4, "note": n(43), "vel": 1.0}
            ]
        }}}),
        json!([{"name": "a", "bars": 4, "play": ["p"]}]),
        json!({}),
    )
}

#[test]
fn overlapping_releases_render_alike_at_any_block_size() {
    let s = piled_releases();
    let k = s.sounds.len();
    let n = (2 * BAR + BAR / 2) as usize;
    let (want, want_stems) = play(&mut player(&s), k, n, 4096);
    assert!(want_stems.iter().all(|x| x.iter().any(loud)));
    for block in [1, 128] {
        let (got, stems) = play(&mut player(&s), k, n, block);
        for (sound, (g, w)) in stems.iter().zip(&want_stems).enumerate() {
            assert!(
                bits(g) == bits(w),
                "block {block}: {} differs",
                s.sounds[sound].name
            );
        }
        assert_eq!(bits(&got), bits(&want), "block {block}");
    }
}

#[test]
fn seek_into_overlapping_releases_plays_as_the_whole_render() {
    let s = piled_releases();
    let full = render(&s, RATE, bank()).unwrap();
    let k = s.sounds.len();
    // inside the cut voices' fades on the third chord, later in its releases, and inside
    // the 808's three-note step
    for frame in [2 * BAR + 100, 2 * BAR + 3 * STEP, 4 * STEP + 100] {
        let mut p = player(&s);
        p.seek(frame);
        let n = BAR as usize / 2;
        let (_, stems) = play(&mut p, k, n, 128);
        let at = frame as usize;
        for (sound, (got, want)) in stems.iter().zip(&full.stems).enumerate() {
            assert!(
                bits(got) == bits(&want[at..at + n]),
                "{} differs after seek({frame})",
                s.sounds[sound].name
            );
        }
    }
}

/// The largest step between neighbouring samples, either channel.
fn largest_jump(x: &[[f32; 2]]) -> f32 {
    x.windows(2)
        .map(|w| (w[1][0] - w[0][0]).abs().max((w[1][1] - w[0][1]).abs()))
        .fold(0.0, f32::max)
}

#[test]
fn a_second_swap_mid_fade_does_not_click() {
    let s = score(
        json!({"pad": {"instrument": "saw", "params": {"voices": 1, "cutoff": 100}}}),
        json!({"p": {"bars": 1, "rows": {"pad": [{"step": 0, "len": 16, "note": n(33), "vel": 1.0}]}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({}),
    );
    let swap = 24000;
    let mut p = player(&s);
    let (before, _) = play(&mut p, 1, swap, 128);
    let steady = largest_jump(&before[swap / 2..]);
    assert!(steady > 1e-4, "{steady}");
    p.set_rack(
        0,
        reset_rack(json!([{"effect": "gain", "params": {"db": -20}}])),
    )
    .unwrap();
    // 3 ms in, the first rack is still heard most
    let (first, _) = play(&mut p, 1, 144, 16);
    p.set_rack(
        0,
        reset_rack(json!([{"effect": "gain", "params": {"db": -6}}])),
    )
    .unwrap();
    let (second, _) = play(&mut p, 1, 2000, 16);
    let mut joined = vec![before[swap - 1]];
    joined.extend_from_slice(&first);
    joined.extend_from_slice(&second);
    let jump = largest_jump(&joined);
    // the weights move by at most 1/480 a frame on a signal no louder than before
    let limit = 1.5 * steady;
    assert!(jump < limit, "a jump of {jump} against {limit}");
}

#[test]
fn end_is_where_the_last_voice_falls_silent() {
    let short = score(
        json!({"hat": {"instrument": "hat_closed"}}),
        json!({"p": {"bars": 1, "rows": {"hat": "x..............."}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({}),
    );
    assert_eq!(player(&short).end(), BAR);

    let release = 3.0;
    let s = score(
        json!({"pad": {"instrument": "saw", "params": {"voices": 1, "release": release}}}),
        json!({"p": {"bars": 1, "rows": {"pad": [{"step": 8, "len": 8, "note": n(45), "vel": 1.0}]}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
        json!({}),
    );
    let mut p = player(&s);
    let end = p.end();
    let rang = BAR + (release * RATE as f64) as u64;
    assert!(end >= rang, "{end} against {rang}");
    let (mix, _) = play(&mut p, 1, end as usize + 9600, 4096);
    // the release sounds past the song, up to the end and nothing after it
    assert!(mix[(rang - 4800) as usize..rang as usize].iter().any(loud));
    assert!(!mix[end as usize..].iter().any(loud));
    // render runs to the same end
    let r = render(&s, RATE, bank()).unwrap();
    assert!(r.mix.len() as u64 <= end);
    assert_eq!(bits(&r.mix), bits(&mix[..r.mix.len()]));
}
