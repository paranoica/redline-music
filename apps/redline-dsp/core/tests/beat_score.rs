use redline_dsp_core::beat::instruments::{instruments_json, Kind, INSTRUMENTS};
use redline_dsp_core::beat::score::{Hit, Mode, Row, Score, ScoreError};
use redline_dsp_core::effect::make;
use serde_json::{json, Value};

fn full() -> Value {
    json!({
        "version": 1,
        "bpm": 140,
        "swing": 58,
        "seed": 7,
        "key": "F# minor",
        "sounds": {
            "kick": {"instrument": "kick", "params": {"decay": 0.5}},
            "bass": {"instrument": "bass808", "params": {"glide_ms": 120}},
            "snare": {"instrument": "snare"},
            "hat": {"instrument": "hat_closed", "swing": 62},
            "open": {"instrument": "hat_open"},
            "bell": {"instrument": "fm", "params": {"preset": "bell", "ratio": 3.5}},
            "keys": {"instrument": "sampler", "library": "piano_soft"}
        },
        "patterns": {
            "drums_a": {"bars": 1, "rows": {
                "kick": "X... ..x. | ..x. ....",
                "snare": {"steps": "....X.......X..g", "chance": [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,0.5]},
                "hat": "x.x.x.x.x.3.x.x2"
            }},
            "bass_a": {"bars": 2, "rows": {
                "bass": [
                    {"step": 0, "len": 8, "note": "F#1", "vel": 1},
                    {"step": 6, "len": 4, "note": "A1", "vel": 0.9, "passing": true},
                    {"step": 16, "len": 16, "note": "Bb0", "vel": 0.8}
                ]
            }},
            "melody": {"bars": 4, "rows": {
                "bell": [{"step": 0, "len": 4, "note": "C#4", "vel": 0.7}],
                "keys": [{"step": 60, "len": 4, "note": "C-1", "vel": 0.5}]
            }},
            "fill_a": {"bars": 1, "rows": {"snare": "x.x.x.x.4444X.X.", "open": "x..............."}}
        },
        "sections": [
            {"name": "intro", "bars": 4, "play": ["melody"], "fill": "fill_a"},
            {"name": "hook", "bars": 8, "play": ["drums_a", "bass_a", "melody"], "mute": ["keys"]}
        ],
        "mix": {
            "sounds": {
                "kick": {"gain_db": -2, "pan": 0},
                "bass": {"gain_db": -4, "rack": [{"effect": "bass_drive"}, {"effect": "compressor"}],
                         "sidechain": "kick"},
                "hat": {"pan": -0.3}
            },
            "master": [{"effect": "limiter"}]
        }
    })
}

fn errors(score: &Value) -> Vec<ScoreError> {
    match Score::from_json(&score.to_string()) {
        Ok(_) => panic!("accepted {score}"),
        Err(e) => e,
    }
}

fn shown(errs: &[ScoreError]) -> Vec<String> {
    errs.iter().map(|e| e.to_string()).collect()
}

/// The error at exactly `path`; panics with every error when there is none.
fn at(score: &Value, path: &str) -> String {
    let errs = errors(score);
    match errs.iter().find(|e| e.path == path) {
        Some(e) => e.message.clone(),
        None => panic!("no error at {path}: {:?}", shown(&errs)),
    }
}

#[test]
fn a_full_score_parses_into_its_parts() {
    let s = Score::from_json(&full().to_string()).unwrap_or_else(|e| panic!("{:?}", shown(&e)));
    assert_eq!(s.bpm, 140.0);
    assert_eq!(s.swing, 58.0);
    assert_eq!(s.seed, 7);
    assert_eq!(s.key.tonic, 6);
    assert_eq!(s.key.mode, Mode::Minor);
    assert_eq!(s.sounds.len(), 7);
    let bass = s.sound("bass").unwrap();
    assert_eq!(s.sounds[bass].instrument.name, "bass808");
    let glide = s.sounds[bass]
        .instrument
        .params
        .iter()
        .position(|p| p.name == "glide_ms")
        .unwrap();
    assert_eq!(s.sounds[bass].params[glide], 120.0);
    let hat = s.sound("hat").unwrap();
    assert_eq!(s.sounds[hat].swing, Some(62.0));
    assert_eq!(s.sounds[s.sound("kick").unwrap()].swing, None);
    let keys = s.sound("keys").unwrap();
    assert_eq!(s.sounds[keys].library.as_deref(), Some("piano_soft"));
    let bell = s.sound("bell").unwrap();
    let preset = s.sounds[bell]
        .instrument
        .params
        .iter()
        .position(|p| p.name == "preset")
        .unwrap();
    assert_eq!(s.sounds[bell].params[preset], 0.0);

    let drums = s.pattern("drums_a").unwrap();
    let kick = s.sound("kick").unwrap();
    let row = s.patterns[drums]
        .rows
        .iter()
        .find(|(k, _)| *k == kick)
        .map(|(_, r)| r)
        .unwrap();
    let Row::Drum(row) = row else {
        panic!("the kick row is not a drum row")
    };
    assert_eq!(row.steps.len(), 16);
    assert_eq!(row.steps[0], Hit::Accent);
    assert_eq!(row.steps[6], Hit::Hit);
    assert_eq!(row.steps[1], Hit::Rest);
    let hats = s.patterns[drums]
        .rows
        .iter()
        .find(|(k, _)| *k == hat)
        .map(|(_, r)| r)
        .unwrap();
    let Row::Drum(hats) = hats else { panic!() };
    assert_eq!(hats.steps[10], Hit::Roll(3));
    assert_eq!(hats.steps[15], Hit::Roll(2));

    let bass_a = s.pattern("bass_a").unwrap();
    let Row::Notes(notes) = &s.patterns[bass_a].rows[0].1 else {
        panic!()
    };
    assert_eq!(notes[0].note, 30);
    assert_eq!(notes[1].note, 33);
    assert!(notes[1].passing && !notes[0].passing);
    assert_eq!(notes[2].note, 22);

    assert_eq!(s.sections.len(), 2);
    assert_eq!(s.sections[0].fill, s.pattern("fill_a"));
    assert_eq!(s.sections[1].mute, vec![keys]);
    assert_eq!(s.bars(), 12);
    assert_eq!(s.mix.sounds[bass].sidechain, Some(kick));
    assert_eq!(s.mix.sounds[bass].gain_db, -4.0);
    assert_eq!(s.mix.sounds[hat].pan, -0.3);
    assert_eq!(s.mix.sounds[bell].gain_db, 0.0);
    assert!(s.mix.sounds[bass].rack.contains("bass_drive"));
    assert!(s.mix.master.contains("limiter"));
}

#[test]
fn swing_defaults_to_straight_and_mix_may_be_left_out() {
    let mut v = full();
    v.as_object_mut().unwrap().remove("swing");
    v.as_object_mut().unwrap().remove("mix");
    let s = Score::from_json(&v.to_string()).unwrap();
    assert_eq!(s.swing, 50.0);
    assert!(s
        .mix
        .sounds
        .iter()
        .all(|m| m.gain_db == 0.0 && m.sidechain.is_none()));
}

#[test]
fn a_drum_row_with_the_wrong_step_count_names_the_row() {
    let mut v = full();
    v["patterns"]["drums_a"]["rows"]["hat"] = json!("x.x.x.x.x.x.x.x");
    let errs = errors(&v);
    assert_eq!(
        shown(&errs),
        vec!["patterns.drums_a.rows.hat: 15 steps, a 1-bar pattern needs 16"]
    );
}

#[test]
fn an_unknown_sound_in_a_row_is_refused_with_its_path() {
    let mut v = full();
    v["patterns"]["drums_a"]["rows"]["tom"] = json!("x...............");
    assert_eq!(at(&v, "patterns.drums_a.rows.tom"), "no sound tom");
}

#[test]
fn unknown_fields_are_refused_at_every_level() {
    let mut v = full();
    v["tempo"] = json!(90);
    v["patterns"]["drums_a"]["rows"]["snare"]["accent"] = json!(true);
    v["patterns"]["bass_a"]["rows"]["bass"][0]["slide"] = json!(1);
    v["sections"][1]["repeat"] = json!(2);
    v["mix"]["sounds"]["kick"]["width"] = json!(1);
    v["sounds"]["kick"]["colour"] = json!("red");
    let errs = errors(&v);
    let paths: Vec<&str> = errs.iter().map(|e| e.path.as_str()).collect();
    for p in [
        "tempo",
        "patterns.drums_a.rows.snare.accent",
        "patterns.bass_a.rows.bass[0].slide",
        "sections[1].repeat",
        "mix.sounds.kick.width",
        "sounds.kick.colour",
    ] {
        assert!(paths.contains(&p), "{p} missing in {:?}", shown(&errs));
    }
    assert!(errs.iter().all(|e| e.message == "unknown field"));
    assert_eq!(errs.len(), 6, "{:?}", shown(&errs));
}

#[test]
fn notes_out_of_range_or_badly_named_are_refused() {
    let mut v = full();
    v["patterns"]["bass_a"]["rows"]["bass"][0]["note"] = json!("G#9");
    v["patterns"]["bass_a"]["rows"]["bass"][1]["note"] = json!("H2");
    v["patterns"]["bass_a"]["rows"]["bass"][2]["note"] = json!("Cb-1");
    let errs = errors(&v);
    let paths: Vec<&str> = errs.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(
        paths,
        vec![
            "patterns.bass_a.rows.bass[0].note",
            "patterns.bass_a.rows.bass[1].note",
            "patterns.bass_a.rows.bass[2].note"
        ],
        "{:?}",
        shown(&errs)
    );
    // the ends of MIDI are fine
    let mut v = full();
    v["patterns"]["bass_a"]["rows"]["bass"][0]["note"] = json!("G9");
    v["patterns"]["bass_a"]["rows"]["bass"][1]["note"] = json!("C-1");
    let s = Score::from_json(&v.to_string()).unwrap();
    let Row::Notes(n) = &s.patterns[s.pattern("bass_a").unwrap()].rows[0].1 else {
        panic!()
    };
    assert_eq!((n[0].note, n[1].note), (127, 0));
}

#[test]
fn a_note_past_the_pattern_end_is_refused_and_one_ending_on_it_is_fine() {
    let mut v = full();
    v["patterns"]["bass_a"]["rows"]["bass"][2]["len"] = json!(17);
    let msg = at(&v, "patterns.bass_a.rows.bass[2]");
    assert!(msg.contains("33") && msg.contains("32"), "{msg}");
    let mut v = full();
    v["patterns"]["bass_a"]["rows"]["bass"][2]["len"] = json!(16);
    assert!(Score::from_json(&v.to_string()).is_ok());
    let mut v = full();
    v["patterns"]["bass_a"]["rows"]["bass"][2]["len"] = json!(0);
    at(&v, "patterns.bass_a.rows.bass[2].len");
}

#[test]
fn section_bars_must_be_a_multiple_of_every_played_pattern() {
    let mut v = full();
    v["sections"][1]["bars"] = json!(6);
    let msg = at(&v, "sections[1].bars");
    assert!(msg.contains("melody") && msg.contains('4'), "{msg}");
    assert_eq!(errors(&v).len(), 1, "{:?}", shown(&errors(&v)));
}

#[test]
fn a_fill_must_be_one_bar_and_references_must_exist() {
    let mut v = full();
    v["sections"][0]["fill"] = json!("bass_a");
    v["sections"][1]["play"] = json!(["drums_a", "verse_b"]);
    v["sections"][1]["mute"] = json!(["tom"]);
    let errs = errors(&v);
    let paths: Vec<&str> = errs.iter().map(|e| e.path.as_str()).collect();
    assert!(paths.contains(&"sections[0].fill"), "{:?}", shown(&errs));
    assert!(paths.contains(&"sections[1].play[1]"), "{:?}", shown(&errs));
    assert!(paths.contains(&"sections[1].mute[0]"), "{:?}", shown(&errs));
}

#[test]
fn an_offline_only_effect_in_a_rack_is_refused_with_its_slot() {
    assert!(make("reverse").unwrap().offline_only());
    let mut v = full();
    v["mix"]["sounds"]["bass"]["rack"] = json!([{"effect": "gain"}, {"effect": "reverse"}]);
    let msg = at(&v, "mix.sounds.bass.rack[1]");
    assert!(msg.contains("reverse"), "{msg}");
    let mut v = full();
    v["mix"]["master"] = json!([{"effect": "reverse"}]);
    at(&v, "mix.master[0]");
}

#[test]
fn a_rack_takes_at_most_four_effects_and_its_params_are_checked() {
    let mut v = full();
    v["mix"]["sounds"]["bass"]["rack"] = json!(vec![json!({"effect": "gain"}); 5]);
    at(&v, "mix.sounds.bass.rack");
    let mut v = full();
    v["mix"]["sounds"]["bass"]["rack"] = json!(vec![json!({"effect": "gain"}); 4]);
    assert!(Score::from_json(&v.to_string()).is_ok());
    let mut v = full();
    v["mix"]["sounds"]["bass"]["rack"] = json!([{"effect": "gain", "params": {"db": 100}}]);
    let msg = at(&v, "mix.sounds.bass.rack[0]");
    assert!(msg.contains("100"), "{msg}");
}

#[test]
fn a_sidechain_must_name_another_sound() {
    let mut v = full();
    v["mix"]["sounds"]["bass"]["sidechain"] = json!("bass");
    at(&v, "mix.sounds.bass.sidechain");
    let mut v = full();
    v["mix"]["sounds"]["bass"]["sidechain"] = json!("tom");
    at(&v, "mix.sounds.bass.sidechain");
}

#[test]
fn sixteen_sounds_are_fine_and_seventeen_are_too_many() {
    let mut v = full();
    let sounds = v["sounds"].as_object_mut().unwrap();
    for i in 0..9 {
        sounds.insert(format!("extra_{i}"), json!({"instrument": "rim"}));
    }
    assert_eq!(sounds.len(), 16);
    assert!(Score::from_json(&v.to_string()).is_ok());
    v["sounds"]["extra_9"] = json!({"instrument": "rim"});
    let msg = at(&v, "sounds");
    assert!(msg.contains("17") && msg.contains("16"), "{msg}");
}

#[test]
fn instrument_params_are_checked_by_name_and_range() {
    let mut v = full();
    v["sounds"]["kick"]["params"]["wobble"] = json!(1);
    let msg = at(&v, "sounds.kick.params.wobble");
    assert!(msg.contains("wobble"), "{msg}");
    let mut v = full();
    v["sounds"]["bass"]["params"]["glide_ms"] = json!(5);
    let msg = at(&v, "sounds.bass.params.glide_ms");
    assert!(msg.contains('5') && msg.contains("10"), "{msg}");
    let mut v = full();
    v["sounds"]["bell"]["params"]["preset"] = json!("organ");
    at(&v, "sounds.bell.params.preset");
    let mut v = full();
    v["sounds"]["kick"]["instrument"] = json!("tabla");
    at(&v, "sounds.kick.instrument");
    let mut v = full();
    v["sounds"]["kick"]["library"] = json!("piano_soft");
    at(&v, "sounds.kick.library");
    let mut v = full();
    v["sounds"]["Kick 2"] = json!({"instrument": "kick"});
    at(&v, "sounds.Kick 2");
}

#[test]
fn rows_must_match_the_kind_of_their_sound() {
    let mut v = full();
    v["patterns"]["bass_a"]["rows"]["bass"] = json!("x...............x...............");
    let msg = at(&v, "patterns.bass_a.rows.bass");
    assert!(msg.contains("bass808"), "{msg}");
    let mut v = full();
    v["patterns"]["drums_a"]["rows"]["kick"] =
        json!([{"step": 0, "len": 1, "note": "C2", "vel": 1}]);
    at(&v, "patterns.drums_a.rows.kick");
}

#[test]
fn drum_row_lanes_have_a_value_a_step_inside_their_ranges() {
    let mut v = full();
    v["patterns"]["drums_a"]["rows"]["snare"]["velocity"] = json!(vec![0.5; 15]);
    assert_eq!(
        at(&v, "patterns.drums_a.rows.snare.velocity"),
        "15 values, a 1-bar pattern needs 16"
    );
    let mut v = full();
    let mut pitch = vec![json!(0); 16];
    pitch[3] = json!(25);
    let mut decay = vec![json!(1); 16];
    decay[0] = json!(0.05);
    let mut chance = vec![json!(1); 16];
    chance[15] = json!(-0.1);
    v["patterns"]["drums_a"]["rows"]["snare"]["pitch"] = json!(pitch);
    v["patterns"]["drums_a"]["rows"]["snare"]["decay"] = json!(decay);
    v["patterns"]["drums_a"]["rows"]["snare"]["chance"] = json!(chance);
    let errs = errors(&v);
    let paths: Vec<&str> = errs.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(
        paths,
        vec![
            "patterns.drums_a.rows.snare.pitch[3]",
            "patterns.drums_a.rows.snare.decay[0]",
            "patterns.drums_a.rows.snare.chance[15]"
        ],
        "{:?}",
        shown(&errs)
    );
    let mut v = full();
    v["patterns"]["drums_a"]["rows"]["kick"] = json!("x..q............");
    at(&v, "patterns.drums_a.rows.kick");
}

#[test]
fn several_defects_are_all_reported_at_once() {
    let mut v = full();
    v["bpm"] = json!(300);
    v["key"] = json!("H minor");
    v["patterns"]["drums_a"]["rows"]["hat"] = json!("x.x.");
    v["patterns"]["drums_a"]["rows"]["tom"] = json!("x...............");
    v["sections"][1]["bars"] = json!(6);
    v["mix"]["sounds"]["bass"]["rack"] = json!([{"effect": "reverse"}]);
    let errs = errors(&v);
    let paths: Vec<&str> = errs.iter().map(|e| e.path.as_str()).collect();
    for p in [
        "bpm",
        "key",
        "patterns.drums_a.rows.hat",
        "patterns.drums_a.rows.tom",
        "sections[1].bars",
        "mix.sounds.bass.rack[0]",
    ] {
        assert!(paths.contains(&p), "{p} missing in {:?}", shown(&errs));
    }
    assert_eq!(errs.len(), 6, "{:?}", shown(&errs));
}

#[test]
fn an_eight_bar_pattern_takes_exactly_128_steps() {
    let mut v = full();
    v["patterns"]["long"] = json!({"bars": 8, "rows": {"kick": "x...".repeat(32)}});
    v["sections"][1]["play"] = json!(["long"]);
    assert!(Score::from_json(&v.to_string()).is_ok());
    v["patterns"]["long"]["rows"]["kick"] = json!(format!("{}x", "x...".repeat(32)));
    assert_eq!(
        at(&v, "patterns.long.rows.kick"),
        "129 steps, an 8-bar pattern needs 128"
    );
    let mut v = full();
    v["patterns"]["long"] = json!({"bars": 3, "rows": {}});
    at(&v, "patterns.long.bars");
}

#[test]
fn bpm_takes_40_to_220() {
    for (bpm, ok) in [(40.0, true), (220.0, true), (39.9, false), (220.1, false)] {
        let mut v = full();
        v["bpm"] = json!(bpm);
        let r = Score::from_json(&v.to_string());
        assert_eq!(r.is_ok(), ok, "{bpm}");
        if !ok {
            assert_eq!(r.unwrap_err()[0].path, "bpm");
        }
    }
}

#[test]
fn swing_takes_50_to_75_globally_and_per_sound() {
    let mut v = full();
    v["swing"] = json!(75);
    v["sounds"]["hat"]["swing"] = json!(50);
    assert!(Score::from_json(&v.to_string()).is_ok());
    v["swing"] = json!(49);
    v["sounds"]["hat"]["swing"] = json!(76);
    let errs = errors(&v);
    let paths: Vec<&str> = errs.iter().map(|e| e.path.as_str()).collect();
    assert_eq!(
        paths,
        vec!["swing", "sounds.hat.swing"],
        "{:?}",
        shown(&errs)
    );
}

#[test]
fn keys_name_a_tonic_and_a_mode() {
    for (key, tonic, mode) in [
        ("C major", 0, Mode::Major),
        ("A dorian", 9, Mode::Dorian),
        ("Bb harmonic_minor", 10, Mode::HarmonicMinor),
        ("Cb locrian", 11, Mode::Locrian),
    ] {
        let mut v = full();
        v["key"] = json!(key);
        let s = Score::from_json(&v.to_string()).unwrap();
        assert_eq!((s.key.tonic, s.key.mode), (tonic, mode), "{key}");
    }
    for key in ["F#minor", "C blues", "c major", ""] {
        let mut v = full();
        v["key"] = json!(key);
        at(&v, "key");
    }
}

#[test]
fn the_version_must_be_one_and_sections_must_exist_within_512_bars() {
    let mut v = full();
    v["version"] = json!(2);
    at(&v, "version");
    let mut v = full();
    v["sections"] = json!([]);
    at(&v, "sections");
    let mut v = full();
    v["sections"] = json!([{"name": "a", "bars": 512, "play": ["drums_a"]}]);
    assert!(Score::from_json(&v.to_string()).is_ok());
    v["sections"]
        .as_array_mut()
        .unwrap()
        .push(json!({"name": "b", "bars": 1, "play": []}));
    let msg = at(&v, "sections");
    assert!(msg.contains("513"), "{msg}");
    let mut v = full();
    v.as_object_mut().unwrap().remove("bpm");
    assert_eq!(at(&v, "bpm"), "missing");
}

#[test]
fn text_that_is_not_json_is_one_error() {
    let errs = Score::from_json("{not json").unwrap_err();
    assert_eq!(errs.len(), 1);
}

#[test]
fn the_instrument_catalogue_lists_kinds_voices_and_params() {
    let list: Value = serde_json::from_str(&instruments_json()).unwrap();
    let list = list.as_array().unwrap();
    assert_eq!(list.len(), INSTRUMENTS.len());
    let names: Vec<&str> = list
        .iter()
        .map(|e| e["instrument"].as_str().unwrap())
        .collect();
    for n in [
        "kick",
        "bass808",
        "snare",
        "clap",
        "hat_closed",
        "hat_open",
        "rim",
        "cowbell",
        "fm",
        "pluck",
        "saw",
        "sampler",
        "tom",
        "shaker",
        "crash",
        "snap",
        "perc",
        "reese",
        "vox",
        "flute",
    ] {
        assert!(names.contains(&n), "{n}");
    }
    let reese = INSTRUMENTS.iter().find(|i| i.name == "reese").unwrap();
    assert!(reese.mono && reese.kind == Kind::Note);
    let bass = INSTRUMENTS.iter().find(|i| i.name == "bass808").unwrap();
    assert!(bass.mono && bass.kind == Kind::Note);
    let glide = bass.params.iter().find(|p| p.name == "glide_ms").unwrap();
    assert_eq!((glide.low, glide.high, glide.default), (10.0, 500.0, 80.0));
    let closed = INSTRUMENTS.iter().find(|i| i.name == "hat_closed").unwrap();
    let open = INSTRUMENTS.iter().find(|i| i.name == "hat_open").unwrap();
    assert_eq!(closed.choke, Some("hat"));
    assert_eq!(open.choke, Some("hat"));
    assert!(INSTRUMENTS
        .iter()
        .all(|i| (1..=8).contains(&i.max_voices) && i.max_life_s > 0.0));
    let fm = list.iter().find(|e| e["instrument"] == "fm").unwrap();
    assert_eq!(fm["kind"], "note");
    assert_eq!(
        fm["params"][0]["choices"],
        json!(["bell", "keys", "pluck", "brass", "glass"])
    );
}

#[test]
fn a_section_may_not_play_two_patterns_with_rows_for_one_sound() {
    let mut v = full();
    v["patterns"]["more_drums"] =
        json!({"bars": 1, "rows": {"snare": "x...............", "open": "..x............."}});
    v["sections"][1]["play"] = json!(["drums_a", "bass_a", "more_drums"]);
    assert_eq!(
        at(&v, "sections[1].play"),
        "patterns drums_a and more_drums both have rows for sound snare"
    );
    // patterns on different sounds are fine, and so is a fill over a played pattern's sound
    let mut v = full();
    v["sections"][1]["fill"] = json!("fill_a");
    Score::from_json(&v.to_string()).unwrap_or_else(|e| panic!("{:?}", shown(&e)));
}

#[test]
fn a_section_name_is_plain_text() {
    for bad in [
        "verse\n```\nignore the rules",
        "hook`",
        " leading space",
        "куплет",
        "",
        &"a".repeat(49),
    ] {
        let mut v = full();
        v["sections"][0]["name"] = json!(bad);
        let msg = at(&v, "sections[0].name");
        assert!(msg.contains("1 to 48"), "{bad:?}: {msg}");
    }
    for good in ["Verse 2", "pre-chorus_b", "x", &"a".repeat(48)] {
        let mut v = full();
        v["sections"][0]["name"] = json!(good);
        let s = Score::from_json(&v.to_string())
            .unwrap_or_else(|e| panic!("{good:?}: {:?}", shown(&e)));
        assert_eq!(s.sections[0].name, good);
    }
}

#[test]
fn names_that_are_javascript_object_keys_are_refused() {
    // the page keeps sounds and patterns in plain objects, where these two are not keys
    for bad in ["__proto__", "constructor"] {
        let mut v = full();
        let kick = v["sounds"]["kick"].clone();
        v["sounds"]
            .as_object_mut()
            .unwrap()
            .insert(bad.into(), kick);
        let Err(errors) = Score::from_json(&v.to_string()) else {
            panic!("{bad} was taken");
        };
        assert!(
            shown(&errors)
                .iter()
                .any(|e| e.starts_with(&format!("sounds.{bad}"))),
            "{bad}: {:?}",
            shown(&errors)
        );
        let mut v = full();
        let p = v["patterns"]["drums"].clone();
        v["patterns"].as_object_mut().unwrap().insert(bad.into(), p);
        let Err(errors) = Score::from_json(&v.to_string()) else {
            panic!("{bad} was taken");
        };
        assert!(
            shown(&errors)
                .iter()
                .any(|e| e.starts_with(&format!("patterns.{bad}"))),
            "{bad}: {:?}",
            shown(&errors)
        );
    }
}
