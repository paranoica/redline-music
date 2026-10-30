//! The score laid out once as events at absolute frames: swing, rolls and chance applied,
//! and the cuts of chokes, stolen voices and mono glides resolved, so a voice can be told
//! everything it plays from its event alone.

use super::instruments::{Instrument, Kind};
use super::score::{Pattern, Row, Score, STEPS_PER_BAR};
use serde_json::json;

#[derive(Clone, Debug, PartialEq)]
pub struct Event {
    /// The event's place in the table.
    pub id: u32,
    pub sound: u32,
    pub start: u64,
    /// Where a note's release begins; None for a drum hit.
    pub off: Option<u64>,
    /// Where a choke, a stolen voice or the next note of a mono sound cuts it.
    pub end: Option<u64>,
    /// MIDI; a drum hit's is 60 plus the step's pitch.
    pub note: f32,
    pub vel: f32,
    /// A scale on the instrument's decay.
    pub decay: f32,
    /// The hit's index within its roll.
    pub roll: u8,
    /// The note a mono sound glides from: the one this event overlapped and cut.
    pub legato_from: Option<f32>,
    pub section: u32,
    /// Bar of the song.
    pub bar: u32,
    /// Step within the bar.
    pub step: u32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct EventTable {
    pub events: Vec<Event>,
    /// Frames of the whole song.
    pub length: u64,
    pub rate: f32,
}

fn mix64(z: u64) -> u64 {
    let z = z.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    let z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// A splitmix64 hash of the words in order. Draws are taken from where they happen, never
/// from a running generator, so leaving out a bar cannot change a later one.
pub fn hash(words: &[u64]) -> u64 {
    words
        .iter()
        .fold(0x243F_6A88_85A3_08D3, |h, &w| mix64(h ^ w))
}

/// A hash mapped onto `0.0..1.0`.
pub fn unit(h: u64) -> f64 {
    (h >> 11) as f64 / (1u64 << 53) as f64
}

impl EventTable {
    /// The table as JSON, for the checks in Python and the editor's drawing:
    /// `{"length", "rate", "key": {"tonic", "mode"}, "sounds": [{"name", "instrument"}],
    /// "events": [{every field of Event}]}`.
    pub fn to_json(&self, score: &Score) -> String {
        let sounds: Vec<_> = score
            .sounds
            .iter()
            .map(|s| json!({"name": s.name, "instrument": s.instrument.name}))
            .collect();
        let events: Vec<_> = self
            .events
            .iter()
            .map(|e| {
                json!({
                    "id": e.id, "sound": e.sound, "start": e.start, "off": e.off, "end": e.end,
                    "note": e.note, "vel": e.vel, "decay": e.decay, "roll": e.roll,
                    "legato_from": e.legato_from, "section": e.section, "bar": e.bar,
                    "step": e.step,
                })
            })
            .collect();
        json!({
            "length": self.length,
            "rate": self.rate,
            "key": {"tonic": score.key.tonic, "mode": score.key.mode.name()},
            "sounds": sounds,
            "events": events,
        })
        .to_string()
    }

    pub fn build(score: &Score, rate: f32) -> EventTable {
        // frames a 16th
        let step = rate as f64 * 60.0 / score.bpm / 4.0;
        let swing: Vec<f64> = score
            .sounds
            .iter()
            .map(|s| s.swing.unwrap_or(score.swing))
            .collect();
        // every time from the step's index, so nothing drifts over a long song
        let time = |k: usize, at: u64| -> f64 {
            let t = at as f64 * step;
            if at % 2 == 1 {
                t + (2.0 * swing[k] / 100.0 - 1.0) * step
            } else {
                t
            }
        };
        let frame = |t: f64| t.round() as u64;

        let mut events = Vec::new();
        let mut first_bar: u64 = 0;
        for (si, section) in score.sections.iter().enumerate() {
            for b in 0..section.bars {
                let fill = if b + 1 == section.bars {
                    section.fill.map(|f| &score.patterns[f])
                } else {
                    None
                };
                let filled = |k: usize| fill.is_some_and(|f| f.rows.iter().any(|r| r.0 == k));
                let mut sources: Vec<(&Pattern, u32, bool)> = section
                    .play
                    .iter()
                    .map(|&p| &score.patterns[p])
                    .map(|p| (p, b % p.bars, false))
                    .collect();
                if let Some(f) = fill {
                    sources.push((f, 0, true));
                }
                let bar = first_bar + b as u64;
                for (pattern, pb, is_fill) in sources {
                    for (k, row) in &pattern.rows {
                        let k = *k;
                        if section.mute.contains(&k) || (!is_fill && filled(k)) {
                            continue;
                        }
                        let at = |s: u32| bar * STEPS_PER_BAR as u64 + s as u64;
                        let base = Event {
                            id: 0,
                            sound: k as u32,
                            start: 0,
                            off: None,
                            end: None,
                            note: 60.0,
                            vel: 0.0,
                            decay: 1.0,
                            roll: 0,
                            legato_from: None,
                            section: si as u32,
                            bar: bar as u32,
                            step: 0,
                        };
                        match row {
                            Row::Drum(row) => {
                                for s in 0..STEPS_PER_BAR {
                                    let i = (pb * STEPS_PER_BAR + s) as usize;
                                    let hit = row.steps[i];
                                    let n = hit.hits();
                                    if n == 0 {
                                        continue;
                                    }
                                    let chance = row.chance.as_ref().map_or(1.0, |c| c[i]);
                                    let draw = unit(hash(&[
                                        score.seed as u64,
                                        si as u64,
                                        b as u64,
                                        k as u64,
                                        s as u64,
                                    ]));
                                    if draw >= chance as f64 {
                                        continue;
                                    }
                                    let t = time(k, at(s));
                                    for r in 0..n {
                                        events.push(Event {
                                            start: frame(t + r as f64 * step / n as f64),
                                            note: 60.0 + row.pitch.as_ref().map_or(0.0, |p| p[i]),
                                            vel: row
                                                .velocity
                                                .as_ref()
                                                .map_or(hit.velocity(), |v| v[i]),
                                            decay: row.decay.as_ref().map_or(1.0, |d| d[i]),
                                            roll: r as u8,
                                            step: s,
                                            ..base.clone()
                                        });
                                    }
                                }
                            }
                            Row::Notes(notes) => {
                                for note in notes.iter().filter(|n| n.step / STEPS_PER_BAR == pb) {
                                    let s = note.step % STEPS_PER_BAR;
                                    events.push(Event {
                                        start: frame(time(k, at(s))),
                                        off: Some(frame(time(k, at(s) + note.len as u64))),
                                        note: note.note as f32,
                                        vel: note.vel,
                                        step: s,
                                        ..base.clone()
                                    });
                                }
                            }
                        }
                    }
                }
            }
            first_bar += section.bars as u64;
        }
        // stable: events on one frame keep the order they were laid out in
        events.sort_by_key(|e| (e.start, e.sound));
        for (i, e) in events.iter_mut().enumerate() {
            e.id = i as u32;
        }
        let instruments: Vec<&Instrument> = score.sounds.iter().map(|s| s.instrument).collect();
        cut(&mut events, &instruments, rate);
        EventTable {
            events,
            length: frame(score.bars() as f64 * STEPS_PER_BAR as f64 * step),
            rate,
        }
    }
}

/// The frame a hit falls silent by itself: its instrument's longest life from the hit (a
/// drum) or from the note's end.
fn natural_end(e: &Event, inst: &Instrument, rate: f32) -> u64 {
    let life = inst.max_life_s as f64 * rate as f64;
    match (inst.kind, e.off) {
        (Kind::Note, Some(off)) => off + life.round() as u64,
        _ => e.start + (life * e.decay as f64).round() as u64,
    }
}

fn rings(e: &Event, inst: &Instrument, rate: f32, t: u64) -> bool {
    e.end.is_none_or(|end| end > t) && natural_end(e, inst, rate) > t
}

/// Chokes, mono glides and stolen voices, in the order the events start.
fn cut(events: &mut [Event], instruments: &[&Instrument], rate: f32) {
    let mut held: Vec<Vec<usize>> = vec![Vec::new(); instruments.len()];
    let mut choked: Vec<usize> = Vec::new();
    for i in 0..events.len() {
        let t = events[i].start;
        let k = events[i].sound as usize;
        let inst = instruments[k];
        let inst_of = |e: &Event| instruments[e.sound as usize];

        if let Some(group) = inst.choke {
            for &j in &choked {
                let other = inst_of(&events[j]);
                if other.choke == Some(group)
                    && events[j].start < t
                    && rings(&events[j], other, rate, t)
                {
                    events[j].end = Some(t);
                }
            }
            choked.retain(|&j| rings(&events[j], inst_of(&events[j]), rate, t));
        }

        held[k].retain(|&j| rings(&events[j], inst, rate, t));
        if inst.mono {
            if let Some(&j) = held[k].last() {
                let glides = events[j].start < t && events[j].off.is_some_and(|off| off > t);
                if glides {
                    events[i].legato_from = Some(events[j].note);
                }
                events[j].end = Some(t);
            }
            held[k].clear();
        } else if held[k].len() >= inst.max_voices {
            let oldest = held[k].remove(0);
            events[oldest].end = Some(t);
        }
        held[k].push(i);
        if inst.choked {
            choked.push(i);
        }
    }
}
