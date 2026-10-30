# Sound

How Redline turns a Suno take into a track that sits closer to the references, and what each
part of the chain is for. Commands are in the [README](../README.md).

## Separation and measurement

Every file, the take and each reference, is split into a vocal and a beat by MelBand Roformer
(`audio-separator`). The model file is pinned by hash on its first download; a changed file is
refused. Stems are cached in `work/cache/` by the file's content and the separator's id, and
measurements by the content and a fingerprint of the measuring code, so a code change re-measures
and nothing else does.

Measurements cover the mix, the vocal, the beat and the pair:

- loudness (integrated, short-term spread, range), true peak, peak-to-loudness, crest;
- spectral tilt and the band limit of the file (an MP3 reference cuts off around 16 kHz, and
  every comparison stays below the lowest limit);
- stereo: correlation and side against mid by band;
- the low end: its share, crest and kurtosis, which tell a clipped 808 from a clean one;
- the vocal: tuning against A440, how long notes hold steady, reverb tail decay;
- the vocal against the beat in LU.

`redline measure` writes a report of where the take stands in the references' range for each.

## The chain

Settings live in `apps/redline/engine/settings.py`, each with its range, default and a label
for the studio. The defaults are what the blind rounds picked by ear.

| Group | Settings |
|---|---|
| chain | compressor (threshold, ratio, attack, release), drive, soft clip, vocal level against the beat |
| eq | how far to fit the vocal's curve to the references, then six bands by hand |
| voice | hard tune strength, formant ratio, the whole voice an octave up, nightcore |
| layers | octave down and up, two wandering doubles, bitcrush |
| fx | stutter, delay throws (ping-pong), reverb; all keyed to phrase ends |
| beat | beat level, 808 band and its drive |
| master | on or off, target loudness, or the references' median |

Hard tune and formant shift run in Praat on the mid channel; the side keeps the stem's width.
Praat's random generator is seeded under a lock, so the same settings give the same file.

Phrases are found once per render: frames within 30 dB of the vocal's loud level are voiced,
and a pause of 200 ms or longer ends a phrase. Throws, reverb and stutter all use that list.

The master clips the mix to the target with oversampling, so the peaks the references have
squared off are squared off here too, at their loudness.

## The engine and the studio

The render runs in stages: chain, voice, layers, effects, rack, beat, master. Each stage's
result is cached by the settings it depends on (up to 1.5 GB), so moving the master target
re-runs only the master and an effect change does not redo the voice. A newer request cancels
an older one between stages.

A preview renders only the loop, padded on both sides by the longest effect tail, and takes the
last whole render's master gain so that it sounds as loud as the whole would. The whole track
renders on a button.

## The rack

The rack stage runs the vocal, after the built-in effects, through the Rust effects of
`apps/redline-dsp` (`redline_dsp.rack_process`). A rack that is empty or has every effect
switched off skips the stage, so its output and the cache are what they were before the rack
existed. The stage is cached by the rack and the beat stage's settings: the processed beat is
the rack's side signal, which `duck`, a compressor with `detector: side` and a vocoder with
`carrier: side` listen to.

Settings files of version 2 carry the rack:

```text
"rack": [{"effect": NAME, "on": true, "params": {PARAM: VALUE},
          "automation": [{"param": PARAM, "points": [[SECONDS, VALUE], ...]}]}]
```

Loading checks every effect and param against `redline_dsp.rack_effects()`, the same list
`redline effects` prints: unknown names, values out of range, a choice that does not exist,
lanes with points out of order or on a choice param are refused with the effect's place in the
rack. Without the Rust module a rack that is not empty is refused; an empty one loads. A file
without `version` (or with version 1) loads with an empty rack, and a rack in it is refused
rather than dropped.

The audio goes through in blocks of 512 frames. An effect that looks ahead (the pitch effects,
the limiter) delays its output; the rack sums the delay of the effects switched on, feeds that
much silence after the vocal and drops as much from the front, so the vocal stays on its
frames. Automation is set before each block from the lane's straight line at the block's first
frame (`redline_dsp.rack_process_automated`); a lane is read later by the delay of the effects
ahead of its effect, so a change lands on the vocal's frame at the second it names. The
smoothed params (levels, mixes, frequencies) glide to a new value over about 20 ms, so a jump
does not click; a param that sizes a buffer, such as the limiter's lookahead, takes only its
value at the start. Lanes are in seconds of the
track only; sections of the lyrics are not supported.

Offline-only effects (`reverse`, `reverse_reverb`) need audio ahead of the frame they play;
both the render and the studio's preview have the whole piece in hand, so they work in both.
A preview runs the rack on the loop padded by 10 s on each side, so tails from before the loop
reach into it. It is close, not exact: an effect that counts from the start of its input (the
grid of `chop`, `trance_gate` and `stutter`, the windows of `reverse`) counts from the start of
the padded piece in a preview and from the start of the track in the render; the whole render
is the one to judge.

The studio page talks to a server on `127.0.0.1` only. The address carries a token; the page
drops it from the address bar and sends it with every call. The Host header must be this
machine at this port, bodies are JSON of at most 64 KB, and only the page's own files are
served. The browser opens a private redirect file, so the token never shows on a command line.

Settings saved from the studio go to `work/studio/<take key>/settings.json`; presets to
`work/studio/presets/<name>.json`, holding only what differs from the defaults.

## Batch runs

```sh
uv run redline run --preset "my preset" --track input/takes/ --ref input/refs
```

`--track` takes files and folders. Each take gets `output/<time>-<take>/`. A take that cannot be
read or has no vocal is reported and skipped; the command exits 2 if any take failed.
`--settings FILE` takes a full settings file instead of a preset.

## Your own vocal

```sh
uv run redline voice --vocal input/vocal.wav --beat input/beat.wav --ref input/refs
```

A vocal recorded by hand comes with its beat as two files, so neither is separated: both are
brought to the analysis rate, the vocal is moved by `--offset` seconds (later when positive,
its start dropped when negative), the shorter one is padded with silence, and their sum is
measured as a take would be. Only the references go through the separator. The vocal should
be dry, recorded over the beat and exported from the same session, or the two will not line
up.

Unless `--settings`, `--preset` or `--no-auto` is given, the settings are searched for. Up to
four 8-second windows, spread evenly from the first to the last second the vocal is within
30 dB of its loudest, are rendered while the vocal level, the compressor threshold and the
drive step through a grid, one at a time, twice over. The references were separated from
mastered songs, so each window goes through the master and the master's change of every
sample is put onto the vocal and the beat before they are measured, pooled over the windows,
for the vocal's level against the beat, crest, short-term spread and kurtosis. The distance to
the references is the sum of squared differences from their medians, each in units of the
references' spread on it; a metric the render lost costs more than any realistic distance. The
lowest distance wins. That is closest to the references by measurement; whether it sounds
better is yours to judge. The chosen values, the distance before and after and the values that
ended at the first or last step of their grid are printed and kept under `auto` in
`chain.json`.

`redline studio --vocal ... --beat ...` opens on the same pair. The search runs only when the
pair has no `settings.json` yet and writes its result there, so the studio starts from it and
never replaces what was saved. The pair's key comes from the two files' paths, not the
offset.

## What numbers can and cannot say

Every comparison is made at equal loudness: louder always sounds better, and would hide
everything else. The report says how close the take is to the references' range on each
measurement. It does not say the take sounds better; that is the owner's call, by ear, usually
blind (`redline vocal`, `redline master`).
