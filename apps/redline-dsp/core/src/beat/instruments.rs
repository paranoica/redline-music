//! The instruments a score's sounds are made of, with their params.

use crate::effect::ParamSpec;
use serde::Serialize;

/// What a row of a sound looks like: a step string for a drum, a list of notes otherwise.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Drum,
    Note,
}

#[derive(Debug, Serialize)]
pub struct Instrument {
    #[serde(rename = "instrument")]
    pub name: &'static str,
    pub kind: Kind,
    /// One voice that glides into a note that overlaps the one before.
    pub mono: bool,
    pub max_voices: usize,
    /// A hit of an instrument in a group ends the still-ringing hits of the `choked`
    /// instruments in it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub choke: Option<&'static str>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub choked: bool,
    /// The longest a hit can ring at the params' maxima, in seconds: from the hit for a drum
    /// (times the step's decay scale), from the note's end for a note instrument.
    pub max_life_s: f32,
    pub params: &'static [ParamSpec],
}

const KICK: &[ParamSpec] = &[
    // the body's pitch once the drop has settled
    ParamSpec::range("tune", 30.0, 120.0, 50.0, "Hz"),
    // how far above the body the hit starts
    ParamSpec::range("pitch_drop", 0.0, 48.0, 24.0, "st"),
    // how long the drop takes
    ParamSpec::range("drop_ms", 5.0, 200.0, 40.0, "ms"),
    ParamSpec::range("decay", 0.05, 2.0, 0.4, "s"),
    // the beater's click on top of the body
    ParamSpec::range("click", 0.0, 1.0, 0.3, ""),
    ParamSpec::range("drive", 0.0, 1.0, 0.1, ""),
];

const BASS808: &[ParamSpec] = &[
    // added to every note
    ParamSpec::range("tune", -12.0, 12.0, 0.0, "st"),
    // the fall of a held note
    ParamSpec::range("decay", 0.1, 8.0, 1.5, "s"),
    // the fade after the note's end
    ParamSpec::range("release", 0.005, 1.0, 0.05, "s"),
    ParamSpec::range("drive", 0.0, 1.0, 0.2, ""),
    // the slide into an overlapping note
    ParamSpec::range("glide_ms", 10.0, 500.0, 80.0, "ms"),
    ParamSpec::range("click", 0.0, 1.0, 0.1, ""),
    // a low-pass after the drive
    ParamSpec::range("tone", 100.0, 8000.0, 2000.0, "Hz"),
    // how far above the note a hit starts, falling to it over `punch_ms`
    ParamSpec::range("punch", 0.0, 24.0, 0.0, "st"),
    ParamSpec::range("punch_ms", 5.0, 200.0, 30.0, "ms"),
    // a hard clip after the drive: 0 none, 1 a near square
    ParamSpec::range("clip", 0.0, 1.0, 0.0, ""),
    // a wavefold after the drive
    ParamSpec::range("fold", 0.0, 1.0, 0.0, ""),
];

const SNARE: &[ParamSpec] = &[
    // the shell's pitch
    ParamSpec::range("tune", 120.0, 400.0, 200.0, "Hz"),
    ParamSpec::range("decay", 0.05, 1.0, 0.2, "s"),
    // noise against shell: 0 all shell, 1 all noise
    ParamSpec::range("tone", 0.0, 1.0, 0.6, ""),
    // the attack of the noise
    ParamSpec::range("snap", 0.0, 1.0, 0.5, ""),
];

const CLAP: &[ParamSpec] = &[
    ParamSpec::range("decay", 0.05, 1.0, 0.25, "s"),
    // the gap between the hands
    ParamSpec::range("spread_ms", 5.0, 40.0, 12.0, "ms"),
    // the band-pass centre
    ParamSpec::range("tone", 600.0, 4000.0, 1200.0, "Hz"),
];

const HAT_CLOSED: &[ParamSpec] = &[
    ParamSpec::range("decay", 0.01, 0.3, 0.05, "s"),
    // the high-pass on the metal
    ParamSpec::range("tone", 4000.0, 14000.0, 8000.0, "Hz"),
    // square partials against noise: 0 all noise
    ParamSpec::range("metal", 0.0, 1.0, 0.5, ""),
];

const HAT_OPEN: &[ParamSpec] = &[
    ParamSpec::range("decay", 0.1, 2.0, 0.5, "s"),
    ParamSpec::range("tone", 4000.0, 14000.0, 8000.0, "Hz"),
    ParamSpec::range("metal", 0.0, 1.0, 0.5, ""),
];

const RIM: &[ParamSpec] = &[
    ParamSpec::range("tune", 300.0, 2000.0, 800.0, "Hz"),
    ParamSpec::range("decay", 0.01, 0.3, 0.05, "s"),
];

const COWBELL: &[ParamSpec] = &[
    // the lower of the two square tones; the upper sits a fixed ratio above
    ParamSpec::range("tune", 400.0, 1200.0, 560.0, "Hz"),
    ParamSpec::range("decay", 0.05, 1.0, 0.3, "s"),
];

const FM: &[ParamSpec] = &[
    // the envelope and operator shape the other params adjust
    ParamSpec::choice("preset", &["bell", "keys", "pluck", "brass", "glass"]),
    // the modulator's frequency over the carrier's
    ParamSpec::range("ratio", 0.5, 16.0, 2.0, ""),
    // the depth of the modulation
    ParamSpec::range("index", 0.0, 20.0, 3.0, ""),
    ParamSpec::range("decay", 0.05, 8.0, 1.0, "s"),
    ParamSpec::range("attack", 0.001, 2.0, 0.005, "s"),
    ParamSpec::range("release", 0.005, 4.0, 0.3, "s"),
];

const PLUCK: &[ParamSpec] = &[
    // the time a held string takes to fall by 60 dB
    ParamSpec::range("decay", 0.1, 10.0, 2.0, "s"),
    // the brightness of the excitation and the loop filter
    ParamSpec::range("tone", 0.0, 1.0, 0.5, ""),
    // the damping after the note's end
    ParamSpec::range("release", 0.005, 2.0, 0.1, "s"),
];

const SAW: &[ParamSpec] = &[
    // detuned saws a note, read as a whole number
    ParamSpec::range("voices", 1.0, 9.0, 5.0, "").fixed(),
    // the spread of the outer saws
    ParamSpec::range("detune", 0.0, 50.0, 15.0, "cents"),
    ParamSpec::range("cutoff", 100.0, 16000.0, 4000.0, "Hz"),
    ParamSpec::range("resonance", 0.0, 1.0, 0.1, ""),
    ParamSpec::range("attack", 0.001, 4.0, 0.01, "s"),
    ParamSpec::range("release", 0.005, 8.0, 0.3, "s"),
];

const ONESHOT: &[ParamSpec] = &[
    // added to the step's pitch; 0 plays the recording as it is
    ParamSpec::range("tune", -24.0, 24.0, 0.0, "st"),
    // where the hit is cut, faded over 5 ms; past the recording's end it simply ends
    ParamSpec::range("length", 0.01, 10.0, 10.0, "s"),
    ParamSpec::range("attack", 0.001, 0.1, 0.001, "s"),
];

const SAMPLE808: &[ParamSpec] = &[
    ParamSpec::range("tune", -12.0, 12.0, 0.0, "st"),
    // the fall of a held note on top of the recording's own
    ParamSpec::range("decay", 0.1, 8.0, 8.0, "s"),
    ParamSpec::range("release", 0.005, 1.0, 0.05, "s"),
    ParamSpec::range("glide_ms", 10.0, 500.0, 80.0, "ms"),
];

const TOM: &[ParamSpec] = &[
    ParamSpec::range("tune", 50.0, 400.0, 110.0, "Hz"),
    ParamSpec::range("pitch_drop", 0.0, 24.0, 7.0, "st"),
    ParamSpec::range("decay", 0.05, 2.0, 0.4, "s"),
    ParamSpec::range("drive", 0.0, 1.0, 0.1, ""),
];

const SHAKER: &[ParamSpec] = &[
    ParamSpec::range("decay", 0.01, 0.4, 0.06, "s"),
    // the band-pass centre
    ParamSpec::range("tone", 2000.0, 14000.0, 7000.0, "Hz"),
    // the rise of the grains, a shaker's swing
    ParamSpec::range("attack_ms", 1.0, 30.0, 6.0, "ms"),
];

const CRASH: &[ParamSpec] = &[
    ParamSpec::range("decay", 0.3, 6.0, 2.0, "s"),
    ParamSpec::range("tone", 3000.0, 14000.0, 6000.0, "Hz"),
    ParamSpec::range("metal", 0.0, 1.0, 0.6, ""),
];

const SNAP: &[ParamSpec] = &[
    ParamSpec::range("decay", 0.02, 0.4, 0.08, "s"),
    ParamSpec::range("tone", 1000.0, 6000.0, 2600.0, "Hz"),
];

const PERC: &[ParamSpec] = &[
    // a struck block: its resonance
    ParamSpec::range("tune", 200.0, 3000.0, 900.0, "Hz"),
    ParamSpec::range("decay", 0.01, 1.0, 0.12, "s"),
    // wood against metal: inharmonic partials over the ping
    ParamSpec::range("metal", 0.0, 1.0, 0.2, ""),
];

const REESE: &[ParamSpec] = &[
    ParamSpec::range("tune", -12.0, 12.0, 0.0, "st"),
    // the spread of the two saws
    ParamSpec::range("detune", 0.0, 50.0, 18.0, "cents"),
    ParamSpec::range("cutoff", 60.0, 8000.0, 900.0, "Hz"),
    ParamSpec::range("resonance", 0.0, 1.0, 0.2, ""),
    // a sine an octave below the saws
    ParamSpec::range("sub", 0.0, 1.0, 0.5, ""),
    ParamSpec::range("drive", 0.0, 1.0, 0.3, ""),
    ParamSpec::range("glide_ms", 10.0, 500.0, 90.0, "ms"),
    ParamSpec::range("attack", 0.001, 1.0, 0.005, "s"),
    ParamSpec::range("release", 0.005, 2.0, 0.08, "s"),
];

const VOX: &[ParamSpec] = &[
    // a, e, i, o, u, morphing between neighbours
    ParamSpec::range("vowel", 0.0, 4.0, 0.0, ""),
    // singers a note, read as a whole number
    ParamSpec::range("voices", 1.0, 7.0, 4.0, "").fixed(),
    ParamSpec::range("detune", 0.0, 40.0, 12.0, "cents"),
    ParamSpec::range("breath", 0.0, 1.0, 0.15, ""),
    ParamSpec::range("vibrato", 0.0, 1.0, 0.3, ""),
    ParamSpec::range("attack", 0.01, 4.0, 0.4, "s"),
    ParamSpec::range("release", 0.01, 8.0, 1.2, "s"),
];

const FLUTE: &[ParamSpec] = &[
    ParamSpec::range("breath", 0.0, 1.0, 0.25, ""),
    ParamSpec::range("vibrato", 0.0, 1.0, 0.4, ""),
    ParamSpec::range("vibrato_hz", 1.0, 9.0, 5.0, "Hz"),
    // the overtones over the fundamental
    ParamSpec::range("tone", 0.0, 1.0, 0.5, ""),
    ParamSpec::range("attack", 0.005, 1.0, 0.06, "s"),
    ParamSpec::range("release", 0.01, 3.0, 0.25, "s"),
];

const SAMPLER: &[ParamSpec] = &[
    // added to every note before the zone is picked
    ParamSpec::range("tune", -24.0, 24.0, 0.0, "st"),
    ParamSpec::range("attack", 0.001, 2.0, 0.002, "s"),
    ParamSpec::range("release", 0.005, 4.0, 0.2, "s"),
];

const fn drum(
    name: &'static str,
    max_voices: usize,
    max_life_s: f32,
    params: &'static [ParamSpec],
) -> Instrument {
    Instrument {
        name,
        kind: Kind::Drum,
        mono: false,
        max_voices,
        choke: None,
        choked: false,
        max_life_s,
        params,
    }
}

const fn note(
    name: &'static str,
    max_voices: usize,
    max_life_s: f32,
    params: &'static [ParamSpec],
) -> Instrument {
    Instrument {
        name,
        kind: Kind::Note,
        mono: false,
        max_voices,
        choke: None,
        choked: false,
        max_life_s,
        params,
    }
}

const fn hat(
    name: &'static str,
    choked: bool,
    max_life_s: f32,
    params: &'static [ParamSpec],
) -> Instrument {
    Instrument {
        choke: Some("hat"),
        choked,
        ..drum(name, 4, max_life_s, params)
    }
}

pub static INSTRUMENTS: &[Instrument] = &[
    drum("kick", 4, 2.5, KICK),
    Instrument {
        mono: true,
        ..note("bass808", 1, 1.0, BASS808)
    },
    drum("snare", 4, 1.5, SNARE),
    drum("clap", 4, 1.5, CLAP),
    hat("hat_closed", false, 0.5, HAT_CLOSED),
    hat("hat_open", true, 2.5, HAT_OPEN),
    drum("rim", 4, 0.5, RIM),
    drum("cowbell", 4, 1.5, COWBELL),
    note("fm", 8, 4.0, FM),
    note("pluck", 8, 2.0, PLUCK),
    note("saw", 8, 8.0, SAW),
    note("sampler", 8, 4.0, SAMPLER),
    drum("tom", 4, 2.5, TOM),
    drum("shaker", 4, 0.6, SHAKER),
    drum("crash", 2, 7.0, CRASH),
    drum("snap", 4, 0.6, SNAP),
    drum("perc", 4, 1.2, PERC),
    Instrument {
        mono: true,
        ..note("reese", 1, 2.0, REESE)
    },
    note("vox", 8, 8.0, VOX),
    note("flute", 4, 3.0, FLUTE),
    drum("oneshot", 4, 10.0, ONESHOT),
    Instrument {
        mono: true,
        ..note("sample808", 1, 1.0, SAMPLE808)
    },
];

/// The instruments that play a sound library's zones, named by the sound's `library`.
pub const SAMPLED: &[&str] = &["sampler", "oneshot", "sample808"];

/// The instrument of that name.
pub fn instrument(name: &str) -> Option<&'static Instrument> {
    INSTRUMENTS.iter().find(|i| i.name == name)
}

/// Every instrument with its params, as JSON:
/// `[{"instrument", "kind", "mono", "max_voices", "choke"?, "choked"?, "max_life_s",
/// "params": [...]}]`.
pub fn instruments_json() -> String {
    serde_json::to_string(INSTRUMENTS).expect("specs serialise")
}
