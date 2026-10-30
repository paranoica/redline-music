use redline_dsp_core::beat::events::{hash, unit, Event, EventTable};
use redline_dsp_core::beat::score::Score;
use serde_json::{json, Value};

const RATE: f32 = 48000.0;
/// A 16th at 120 bpm and 48 kHz.
const STEP: u64 = 6000;

fn score_with(swing: f64, sounds: Value, patterns: Value, sections: Value) -> Score {
    let v = json!({
        "version": 1, "bpm": 120, "swing": swing, "seed": 11, "key": "C minor",
        "sounds": sounds, "patterns": patterns, "sections": sections
    });
    Score::from_value(&v).unwrap_or_else(|e| panic!("{:?}", e))
}

fn score(sounds: Value, patterns: Value, sections: Value) -> Score {
    score_with(50.0, sounds, patterns, sections)
}

fn one_row(instrument: &str, row: Value, bars: u32) -> Score {
    score(
        json!({"s": {"instrument": instrument}}),
        json!({"p": {"bars": bars, "rows": {"s": row}}}),
        json!([{"name": "a", "bars": bars, "play": ["p"]}]),
    )
}

fn table(s: &Score) -> EventTable {
    EventTable::build(s, RATE)
}

fn starts(events: &[&Event]) -> Vec<u64> {
    events.iter().map(|e| e.start).collect()
}

fn of(t: &EventTable, s: &Score, name: &str) -> Vec<Event> {
    let k = s.sound(name).unwrap() as u32;
    t.events.iter().filter(|e| e.sound == k).cloned().collect()
}

#[test]
fn a_sixteenth_at_120_bpm_is_6000_frames() {
    let s = one_row("hat_closed", json!("xxxx xxxx xxxx xxxx"), 1);
    let t = table(&s);
    let got: Vec<u64> = t.events.iter().map(|e| e.start).collect();
    let want: Vec<u64> = (0..16).map(|k| k * STEP).collect();
    assert_eq!(got, want);
    assert!(t.events.iter().all(|e| e.end.is_none() && e.off.is_none()));
}

#[test]
fn swing_delays_the_second_sixteenth_of_each_pair() {
    let s = score_with(
        66.0,
        json!({"s": {"instrument": "hat_closed"}}),
        json!({"p": {"bars": 1, "rows": {"s": "xxxx............"}}}),
        json!([{"name": "a", "bars": 2, "play": ["p"]}]),
    );
    let t = table(&s);
    let got: Vec<u64> = t.events.iter().map(|e| e.start).collect();
    let late = 7920.0;
    let want = [0.0, late, 2.0 * 6000.0, 3.0 * 6000.0 + 1920.0];
    for (g, w) in got.iter().zip(want) {
        assert!((*g as f64 - w).abs() <= 1.0, "{got:?}");
    }
    // the second bar lands on its grid, not on an accumulated drift
    assert_eq!(got[4], 16 * STEP);
    assert!((got[5] as f64 - (17.0 * 6000.0 + 1920.0)).abs() <= 1.0);
}

#[test]
fn a_sound_with_its_own_swing_ignores_the_global_one() {
    let s = score(
        json!({"a": {"instrument": "hat_closed", "swing": 66}, "b": {"instrument": "rim"}}),
        json!({"p": {"bars": 1, "rows": {"a": "xx..............", "b": "xx.............."}}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
    );
    let t = table(&s);
    let a = of(&t, &s, "a");
    let b = of(&t, &s, "b");
    assert!((a[1].start as i64 - 7920).abs() <= 1);
    assert_eq!(b[1].start, STEP);
}

#[test]
fn a_roll_splits_its_step_evenly() {
    let s = one_row("snare", json!("3...4..........."), 1);
    let t = table(&s);
    let ev: Vec<&Event> = t.events.iter().collect();
    assert_eq!(
        starts(&ev),
        vec![
            0,
            2000,
            4000,
            4 * STEP,
            4 * STEP + 1500,
            4 * STEP + 3000,
            4 * STEP + 4500
        ]
    );
    let rolls: Vec<u8> = ev.iter().map(|e| e.roll).collect();
    assert_eq!(rolls, vec![0, 1, 2, 0, 1, 2, 3]);
}

#[test]
fn accents_ghosts_and_lanes_set_velocity_pitch_and_decay() {
    let s = one_row("snare", json!("Xxg............."), 1);
    let t = table(&s);
    let vel: Vec<f32> = t.events.iter().map(|e| e.vel).collect();
    assert_eq!(vel, vec![1.0, 0.8, 0.4]);
    assert!(t.events.iter().all(|e| e.note == 60.0 && e.decay == 1.0));

    let mut velocity = vec![json!(0.5); 16];
    velocity[0] = json!(0.25);
    let mut pitch = vec![json!(0); 16];
    pitch[1] = json!(-12);
    let mut decay = vec![json!(1); 16];
    decay[2] = json!(2.5);
    let s = one_row(
        "snare",
        json!({"steps": "Xxg.............", "velocity": velocity, "pitch": pitch, "decay": decay}),
        1,
    );
    let t = table(&s);
    let vel: Vec<f32> = t.events.iter().map(|e| e.vel).collect();
    assert_eq!(vel, vec![0.25, 0.5, 0.5]);
    let note: Vec<f32> = t.events.iter().map(|e| e.note).collect();
    assert_eq!(note, vec![60.0, 48.0, 60.0]);
    let dec: Vec<f32> = t.events.iter().map(|e| e.decay).collect();
    assert_eq!(dec, vec![1.0, 1.0, 2.5]);
}

fn chance_row(chance: f64, steps: &str) -> Value {
    let n = steps.chars().filter(|c| !matches!(c, ' ' | '|')).count();
    json!({"steps": steps, "chance": vec![chance; n]})
}

#[test]
fn chance_zero_never_plays_and_one_always_does() {
    let all = "x".repeat(16);
    let s = score(
        json!({"s": {"instrument": "rim"}}),
        json!({"p": {"bars": 1, "rows": {"s": chance_row(0.0, &all)}}}),
        json!([{"name": "a", "bars": 64, "play": ["p"]}]),
    );
    assert!(table(&s).events.is_empty());
    let s = score(
        json!({"s": {"instrument": "rim"}}),
        json!({"p": {"bars": 1, "rows": {"s": chance_row(1.0, &all)}}}),
        json!([{"name": "a", "bars": 64, "play": ["p"]}]),
    );
    assert_eq!(table(&s).events.len(), 64 * 16);
}

#[test]
fn chance_one_half_plays_about_half_the_steps() {
    let all = "x".repeat(16);
    let s = score(
        json!({"s": {"instrument": "rim"}}),
        json!({"p": {"bars": 1, "rows": {"s": chance_row(0.5, &all)}}}),
        json!([{"name": "a", "bars": 512, "play": ["p"]}]),
    );
    let n = table(&s).events.len() as f64 / (512.0 * 16.0);
    assert!((0.45..=0.55).contains(&n), "{n}");
}

#[test]
fn a_draw_does_not_hang_on_the_bars_before_it() {
    // every bar draws in `full`; in `sparse` only bars 1 and 9 have hits
    let all = "x".repeat(16);
    let full = score(
        json!({"s": {"instrument": "rim"}}),
        json!({"p": {"bars": 1, "rows": {"s": chance_row(0.5, &all)}}}),
        json!([{"name": "a", "bars": 16, "play": ["p"]}]),
    );
    let eight = format!("{}{}{}", ".".repeat(16), all, ".".repeat(96));
    let sparse = score(
        json!({"s": {"instrument": "rim"}}),
        json!({"p": {"bars": 8, "rows": {"s": chance_row(0.5, &eight)}}}),
        json!([{"name": "a", "bars": 16, "play": ["p"]}]),
    );
    let bar9 = |t: &EventTable| -> Vec<u64> {
        t.events
            .iter()
            .filter(|e| e.bar == 9)
            .map(|e| e.start)
            .collect()
    };
    let a = bar9(&table(&full));
    let b = bar9(&table(&sparse));
    assert!(!a.is_empty() && a.len() < 16, "{a:?}");
    assert_eq!(a, b);
    assert!(table(&sparse)
        .events
        .iter()
        .all(|e| e.bar == 1 || e.bar == 9));
}

#[test]
fn hash_is_a_pure_function_of_its_words() {
    assert_eq!(hash(&[1, 2, 3]), hash(&[1, 2, 3]));
    assert_ne!(hash(&[1, 2, 3]), hash(&[1, 2, 4]));
    assert_ne!(hash(&[1, 2, 3]), hash(&[2, 1, 3]));
    let u = unit(hash(&[5]));
    assert!((0.0..1.0).contains(&u));
}

fn drums_with_fill(mute: Value) -> Score {
    score(
        json!({"kick": {"instrument": "kick"}, "snare": {"instrument": "snare"}}),
        json!({
            "beat": {"bars": 1, "rows": {"kick": "x.......x.......", "snare": "....x.......x..."}},
            "roll": {"bars": 1, "rows": {"snare": "....xxxx4444xxxx"}}
        }),
        json!([
            {"name": "a", "bars": 4, "play": ["beat"], "fill": "roll", "mute": mute},
            {"name": "b", "bars": 1, "play": ["beat"]}
        ]),
    )
}

#[test]
fn a_fill_replaces_the_last_bar_for_its_own_sounds_only() {
    let s = drums_with_fill(json!([]));
    let t = table(&s);
    let bar = |name: &str, b: u32| -> Vec<u64> {
        of(&t, &s, name)
            .iter()
            .filter(|e| e.bar == b)
            .map(|e| (e.start - b as u64 * 16 * STEP) / STEP)
            .collect()
    };
    for b in 0..3 {
        assert_eq!(bar("snare", b), vec![4, 12], "bar {b}");
    }
    assert_eq!(
        bar("snare", 3),
        vec![4, 5, 6, 7, 8, 8, 8, 8, 9, 9, 9, 9, 10, 10, 10, 10, 11, 11, 11, 11, 12, 13, 14, 15]
    );
    for b in 0..5 {
        assert_eq!(bar("kick", b), vec![0, 8], "bar {b}");
    }
    // the next section has no fill
    assert_eq!(bar("snare", 4), vec![4, 12]);
    let sections: Vec<u32> = of(&t, &s, "kick").iter().map(|e| e.section).collect();
    assert_eq!(sections, vec![0, 0, 0, 0, 0, 0, 0, 0, 1, 1]);
}

#[test]
fn a_mute_removes_the_sound_and_its_fill_from_the_section() {
    let s = drums_with_fill(json!(["snare"]));
    let t = table(&s);
    let snares: Vec<u32> = of(&t, &s, "snare").iter().map(|e| e.bar).collect();
    assert_eq!(snares, vec![4, 4]);
    assert_eq!(of(&t, &s, "kick").len(), 10);
}

fn bass(notes: Value, bars: u32) -> (Score, EventTable) {
    let s = one_row("bass808", notes, bars);
    let t = table(&s);
    (s, t)
}

#[test]
fn an_overlapping_808_note_glides_from_the_one_it_cuts() {
    let (_, t) = bass(
        json!([
            {"step": 0, "len": 8, "note": "C1", "vel": 1},
            {"step": 4, "len": 4, "note": "F1", "vel": 1}
        ]),
        1,
    );
    let e = &t.events;
    assert_eq!(e.len(), 2);
    assert_eq!((e[0].note, e[1].note), (24.0, 29.0));
    assert_eq!(e[0].off, Some(8 * STEP));
    assert_eq!(e[0].end, Some(4 * STEP));
    assert_eq!(e[0].legato_from, None);
    assert_eq!(e[1].legato_from, Some(24.0));
    assert_eq!(e[1].off, Some(8 * STEP));
    assert_eq!(e[1].end, None);
}

#[test]
fn an_808_note_after_a_gap_does_not_glide() {
    let (_, t) = bass(
        json!([
            {"step": 0, "len": 2, "note": "C1", "vel": 1},
            {"step": 24, "len": 2, "note": "F1", "vel": 1}
        ]),
        2,
    );
    let e = &t.events;
    assert_eq!(e[1].legato_from, None);
    assert_eq!(e[0].end, None);
    assert_eq!(e[0].off, Some(2 * STEP));
}

#[test]
fn a_note_that_starts_as_the_last_ends_does_not_glide() {
    let (_, t) = bass(
        json!([
            {"step": 0, "len": 4, "note": "C1", "vel": 1},
            {"step": 4, "len": 4, "note": "F1", "vel": 1}
        ]),
        1,
    );
    assert_eq!(t.events[1].legato_from, None);
}

#[test]
fn a_closed_hat_chokes_an_open_one_and_a_snare_does_not() {
    let s = score(
        json!({
            "open": {"instrument": "hat_open"},
            "closed": {"instrument": "hat_closed"},
            "snare": {"instrument": "snare"}
        }),
        json!({"p": {"bars": 1, "rows": {
            "open": "x.......x.......",
            "closed": "....x...........",
            "snare": "..........x....."
        }}}),
        json!([{"name": "a", "bars": 1, "play": ["p"]}]),
    );
    let t = table(&s);
    let open = of(&t, &s, "open");
    assert_eq!(open[0].end, Some(4 * STEP));
    // the snare at step 10 leaves the second open hat ringing
    assert_eq!(open[1].end, None);
    assert!(of(&t, &s, "closed").iter().all(|e| e.end.is_none()));
    assert!(of(&t, &s, "snare").iter().all(|e| e.end.is_none()));
}

#[test]
fn an_open_hat_chokes_the_open_hat_before_it() {
    let s = one_row("hat_open", json!("x...x..........."), 1);
    let t = table(&s);
    assert_eq!(t.events[0].end, Some(4 * STEP));
    assert_eq!(t.events[1].end, None);
}

#[test]
fn a_ninth_overlapping_voice_steals_the_oldest() {
    let notes: Vec<Value> = (0..9)
        .map(|k| json!({"step": k, "len": 16 - k, "note": "C4", "vel": 0.5}))
        .collect();
    let s = one_row("fm", json!(notes), 1);
    let t = table(&s);
    assert_eq!(t.events.len(), 9);
    assert_eq!(t.events[0].end, Some(8 * STEP));
    assert!(t.events[1..].iter().all(|e| e.end.is_none()));
    // eight overlapping voices steal nothing
    let s = one_row("fm", json!(notes[..8]), 1);
    assert!(table(&s).events.iter().all(|e| e.end.is_none()));
}

#[test]
fn the_song_is_as_long_as_its_sections() {
    let s = drums_with_fill(json!([]));
    let t = table(&s);
    assert_eq!(t.length, 5 * 16 * STEP);
    let s = one_row("kick", json!("x...............".repeat(8)), 8);
    assert_eq!(table(&s).length, 8 * 16 * STEP);
}

#[test]
fn events_are_sorted_with_ids_in_their_order() {
    let s = drums_with_fill(json!([]));
    let t = table(&s);
    assert!(t.events.windows(2).all(|w| w[0].start <= w[1].start));
    assert!(t.events.iter().enumerate().all(|(i, e)| e.id == i as u32));
}

#[test]
fn the_same_score_builds_the_same_table() {
    let all = "x".repeat(16);
    let s = score_with(
        61.0,
        json!({"s": {"instrument": "rim"}, "b": {"instrument": "bass808"}}),
        json!({"p": {"bars": 1, "rows": {
            "s": chance_row(0.5, &all),
            "b": [{"step": 0, "len": 8, "note": "C1", "vel": 1},
                  {"step": 6, "len": 4, "note": "D1", "vel": 1}]
        }}}),
        json!([{"name": "a", "bars": 8, "play": ["p"]}]),
    );
    let a = table(&s);
    let b = table(&s);
    assert_eq!(a, b);
    assert!(!a.events.is_empty());
}
