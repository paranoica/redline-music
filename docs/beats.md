# Beats

How `redline beat` turns a seed into a beat, the score format the agent writes, the sounds it
can use, and what the numbers do and do not say.

## The loop

1. `redline beat write --seed N` draws a recipe from the seed: genre, tempo, key and mode,
   swing, a palette of sounds, a structure of sections and two or three moves. `--genre`,
   `--bpm` and `--key` pin a field; the other draws stay the same.
2. The request goes to `private/beats/N/request-1.md`. The `beatmaker` agent reads only that
   file and writes the score to the draft path named at its end.
3. The same command again checks the draft. A valid score becomes `score-1.json`; an invalid
   one sends a new request with every error and warning, at most four rounds. A draft that
   starts with `REFUSED:` stops the seed with its reason.
4. `render`, `measure`, `studio` and `marks` all work on the newest `score-K.json` of the seed.
   The page saves the next number and never writes over an older score.

## Score format

Version 1. Time is counted in steps only, sixteen to a bar, in 4/4; nothing is in seconds.
Unknown fields are refused, and every error names its place, e.g.
`patterns.drums.rows.hat: 15 steps, a 1-bar pattern needs 16`.

```json
{
  "version": 1,
  "bpm": 140,
  "swing": 54,
  "seed": 7,
  "key": "F# minor",
  "sounds": {
    "kick": {"instrument": "kick", "params": {"decay": 0.5}},
    "bass": {"instrument": "bass808", "params": {"glide_ms": 120}},
    "hat":  {"instrument": "hat_closed", "swing": 58}
  },
  "patterns": {
    "drums": {"bars": 1, "rows": {
      "kick": "x... .... x.x. ....",
      "hat":  {"steps": "x.x. x.x. x3x. x.x6", "velocity": [1, 0.6, 1, 0.6, 1, 0.6, 1, 0.6, 1, 0.6, 1, 0.6, 1, 0.6, 1, 0.6]}
    }},
    "bass": {"bars": 2, "rows": {
      "bass": [{"step": 0, "len": 12, "note": "F#1", "vel": 1.0},
               {"step": 10, "len": 6, "note": "C#2", "vel": 0.9}]
    }}
  },
  "sections": [
    {"name": "intro", "bars": 4, "play": ["drums"], "mute": ["kick"]},
    {"name": "hook", "bars": 8, "play": ["drums", "bass"]}
  ],
  "mix": {
    "sounds": {"bass": {"gain_db": -2, "rack": [{"effect": "saturate", "params": {"drive": 0.3}}]}},
    "master": [{"effect": "limiter", "params": {"ceiling": -1}}]
  }
}
```

- `bpm` 40 to 220. `swing` 50 to 75: the share of a pair of 16ths the first one takes, so 50
  is straight and 66 is a triplet feel; a sound may set its own.
- `key`: a tonic and a mode: major, minor, dorian, phrygian, lydian, mixolydian, locrian,
  harmonic_minor.
- Names of sounds and patterns: `a-z`, digits and `_`, up to 24 characters. Section names:
  ASCII letters, digits, space, `_` and `-`, up to 48.
- A pattern has `bars` (1, 2, 4 or 8) and a row per sound.
- A drum row is a string with one character per step: `.` rest, `x` hit, `X` accent, `g`
  ghost, `2 3 4 6` a roll of that many hits spread over the step. `|` and spaces are ignored.
  Its length is 16 times the bars. As an object it may add lanes of the same length:
  `velocity` 0..1, `pitch` -24..24 semitones, `decay` 0.1..4 (a scale on the sound's decay),
  `chance` 0..1.
- A note row is a list of `{step, len, note, vel}`; `note` is a name such as `F#1` or `Bb2`.
  A note may not run past the pattern's end. A note outside the key is refused unless it has
  `"passing": true`.
- The 808 is mono: a note that starts before the previous one ends glides into it, over the
  sound's `glide_ms`. There is no glide flag; the overlap is the glide, and the page draws the
  curve the synth plays.
- A section plays its patterns for `bars` bars, which must be a multiple of each pattern's
  length. `mute` silences sounds, `fill` names a one-bar pattern that replaces the last bar for
  the sounds it has rows for. Two patterns in one section may not both have rows for one sound.
- `mix.sounds.<name>`: `gain_db` -60..12, `pan` -1..1, a `rack` of up to four effects of the
  studio's rack (the offline-only ones are refused, a beat plays live), and `sidechain`, another
  sound whose dry signal feeds the rack's side input. `mix.master` is a rack of up to four.
- Limits: 16 sounds, 64 patterns, 512 notes a row, 512 bars.

Chance is drawn from a hash of the seed, the section, the bar, the sound and the step, so a
render from any bar gives the same hits as the whole render.

## Checks

Errors refuse a score: anything the format refuses, a note out of key, an 808 note below C1 or
above G3 (counting its `tune`), a bar count outside the recipe's range, a tempo other than the
recipe's, a library that is not in the manifest.

Warnings never refuse; they go into the next request and onto the page: hit density per genre,
the same bar repeated more than eight times, the same set of sounds for more than eight bars, a
kick within 10 ms of an 808 note start. Each says where its limit comes from: production blogs,
unverified, or our own choice.

Genres: trap, uk_drill, ny_drill, plugg, boom_bap, phonk, jersey_club, cloud, lofi. Their
tempos, hat styles and 808 habits come from production blogs and are starting points, not rules.

## Instruments

| Instrument | Row | Voices | | Params |
|---|---|---|---|---|
| `kick` | drum | 4 |  | tune 30..120 Hz, pitch_drop 0..48 st, drop_ms 5..200 ms, decay 0.05..2 s, click 0..1, drive 0..1 |
| `bass808` | note | 1 | mono | tune -12..12 st, decay 0.1..8 s, release 0.005..1 s, drive 0..1, glide_ms 10..500 ms, click 0..1, tone 100..8000 Hz |
| `snare` | drum | 4 |  | tune 120..400 Hz, decay 0.05..1 s, tone 0..1, snap 0..1 |
| `clap` | drum | 4 |  | decay 0.05..1 s, spread_ms 5..40 ms, tone 600..4000 Hz |
| `hat_closed` | drum | 4 | choke group hat | decay 0.01..0.3 s, tone 4000..14000 Hz, metal 0..1 |
| `hat_open` | drum | 4 | choke group hat | decay 0.1..2 s, tone 4000..14000 Hz, metal 0..1 |
| `rim` | drum | 4 |  | tune 300..2000 Hz, decay 0.01..0.3 s |
| `cowbell` | drum | 4 |  | tune 400..1200 Hz, decay 0.05..1 s |
| `fm` | note | 8 |  | preset (bell/keys/pluck), ratio 0.5..16, index 0..20, decay 0.05..8 s, attack 0.001..2 s, release 0.005..4 s |
| `pluck` | note | 8 |  | decay 0.1..10 s, tone 0..1, release 0.005..2 s |
| `saw` | note | 8 |  | voices 1..9, detune 0..50 cents, cutoff 100..16000 Hz, resonance 0..1, attack 0.001..4 s, release 0.005..8 s |
| `sampler` | note | 8 |  | tune -24..24 st, attack 0.001..2 s, release 0.005..4 s |

Drums and the 808 are classic analog-style recipes: a sine with a falling pitch for the kick, two
tones and filtered noise for the snare, noise bursts for the clap, six square waves for the hats.
`fm` is two-operator FM with three envelope presets, `pluck` is Karplus-Strong, `saw` a stack of
detuned band-limited saws through a resonant lowpass. A closed hat cuts a ringing open hat.

`sampler` plays a recorded library named by the sound's `library` field.

## Sounds and licences

Synthesized sounds are ours and may be used in anything. Recorded instruments come from
`apps/redline/beat/library.json`, which names for every library its source, the commit it is
pinned to, its licence with a quoted line, and each file with its root note, velocity range,
size and git blob hash. `redline beat sounds fetch <name>` downloads only those files into
`work/sounds/<name>/` and checks each one before writing it. Nothing recorded is committed.

All ten libraries listed now are CC0, from VSCO 2 Community Edition and the Versilian Community
Sample Library. Two more are listed as local only: they are allowed in your own music but not to
be passed on as samples, so the tool never downloads them and marks them as not for sale; you
place them in `work/sounds/<name>/` by hand. A render report lists any sound of that kind.

## The engine

The score is laid out once as events at frame times, with swing, rolls, chance, chokes, the
808's glides and stolen voices resolved. A voice's output depends only on its event and the
frames since it began, so a render from bar 20 matches the whole render from there, and the
page, the Python render and the WebAssembly build give the same samples (racks differ by float
rounding, around 2e-7). Each sound's rack is
delayed to line up with the slowest one, so a compressor on the snare does not pull it late.

Changing a note or a param on the page swaps the event table at a block boundary while ringing
notes keep sounding; a rack change crossfades over 10 ms. A change of sounds rebuilds the
player at the same position.

## Measuring a beat

`redline beat measure` measures the render as a beat (loudness, crest, spectral tilt, stereo
width, the low end's share, crest and kurtosis) and the beat stems split out of the references
the same way. A value counts as outside the references only past their range by half their
spread plus the metric's threshold; the report shows that tolerance per row. The references'
beats come through the separator and the render does not, so tilt and low-end numbers lean
toward the render's side. Numbers say closer or further, never better.

## Marks

`redline beat marks --seed N --bar 5 bad --note "the hats crowd the hook"` or `B` on the page
stores the mark with the bar as written in that score, and its hash. The next request of the
same genre quotes up to four bad and four good bars. A mark from the page goes to the saved
score it was made on; a newer save in between refuses it.
