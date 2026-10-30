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

## The Python engine

The render runs in stages: chain, voice, layers, effects, rack, beat, master. Each stage's
result is cached by the settings it depends on (up to 1.5 GB), so moving the master target
re-runs only the master and an effect change does not redo the voice. A newer request cancels
an older one between stages.

## The live studio and the exact render

The studio page plays the track through three racks in an AudioWorklet, on the Rust core built
for WebAssembly (`apps/redline-dsp/wasm`): the vocal rack (the chain as rack effects: the fitted
EQ, compressor, drive and clip, then voice, layers, effects and the user's rack), the beat rack
(808 drive) and the master rack (the master gain and a clipper at the ceiling). The beat rack's
output, delayed by its own latency, is the vocal rack's side signal; the two are mixed at equal
latency and go through the master. `apps/redline/engine/live.py` turns settings into these racks.

What needs the whole track stays on the server: the EQ fit toward the references, the tuning
and the master gain for the current settings. The page asks for them again when a setting they
depend on changes and keeps playing meanwhile.

The exact render, `redline run` and `redline voice` run the same racks natively
(`apps/redline/engine/racks_render.py`), so the file matches what the page played: a test drives
the page's engine in node block by block and compares it with the file. Where they still differ:

- `octave_down` and the doubles use signalsmith's FFT; the browser build rounds it differently,
  about −89 dB under the signal.
- automation is set every 512 frames in the file and every 128 on the page, so a smooth lane
  steps a little differently.
- the page fades in after a start or a seek and does not play the last frames of the track (the
  racks' latency, about 0.1 s); the file has them.

`--classic` renders with the staged Python engine below instead. The blind rounds, the automatic
settings search and the studio's server analysis still use it, so they sound slightly different
from the racks.

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

Offline-only effects (`reverse`, `reverse_reverb`) need audio ahead of the frame they play.
The render has the whole piece in hand; the live page plays them with a long delay, so judge
them in the exact render.

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

## Recording

`redline record` drives `redline-rec` (`apps/redline-dsp/rec`), a Rust program that is one JACK
client with a mic input and two outputs. Capture and playback run in one process callback, so
they share one frame counter: frame 0 of the beat is the first frame played after a start, and
every mic frame has its index on the same counter. PipeWire is asked for a 64-frame quantum
through `PIPEWIRE_LATENCY`; the rate and quantum it grants are reported, and the beat is
resampled to the granted rate before playing. There is no ALSA fallback: separate input and
output devices run on two clocks that drift apart by milliseconds a minute.

Python talks to the engine in JSON lines over stdin and stdout: start, stop, set the rack, read
the meters. A take is shifted by the round trip, so it lines up with the beat. Nothing in the
callback allocates or locks: the dry mic goes through a lock-free ring to a writer thread, and a
rack built for a new setting is handed in through another ring and the old one handed back to
be dropped outside the callback. When the callback misses cycles or the ring overflows, the
writer puts in silence of exactly the missing length and logs the frame range in the take's
`take-NN.json`, so the rest of the take stays in time.

**The monitor.** You hear the mic through the vocal rack `live.py` makes, without automation and
with `chain_compressor`'s lookahead at 0 (the render keeps its 4 ms). The budget is 10 ms minus
the round trip; when the rack is slower, effects are switched off from its end until it fits,
for monitoring only. The rack's numbers come from `live.analysis` on the song's last take with
the references. Before the first take a 5-second level check sets the input gain and the rest
are neutral (`live.NEUTRAL_NUMBERS`): no gate, no makeup gain, a flat EQ, no tuning offset, so
until there is a take you hear yourself quieter and cleaner than the render will be.

**Calibration.** `redline record --calibrate` plays an exponential sine sweep with the monitor
muted, records it through the singer's path and convolves the recording with the sweep's
inverse filter. The peak is the round trip; a peak less than 30 dB over the median of the rest
is refused (noise alone reaches about 23 dB). The result is stored in `work/record/latency.json`
under the connected ports, the rate and the quantum, with the mode: a headphone cup at the mic,
a cable, or speakers, which adds the time sound takes through the air and is named every time
it is applied. If the quantum changes during a session, the next take is refused until
`redline record` restarts, because the stored round trip no longer holds. A calibration for another
key is refused with a note, never applied; without one the ports' reported latency is used and
labelled an estimate.

**Ranking.** `redline takes` measures each take against the beat and the other takes. Timing is
the onsets' offset from the median of all the takes, so a flow that sits behind the beat in
every take is not punished; a penalty starts 10 ms out. Clipping counts runs at full scale,
noise is the floor between phrases, dropouts come from the engine's log, and bleed is the
take's correlation with the beat (above 0.1 means headphones are leaking). Level steadiness is
a note, not a score. `--deep` adds the distance to the references after the automatic settings
of `redline voice`. A take that is empty or almost silent is skipped. A take shorter than half
the beat is measured over the part it covers and ranked below every full take, since fewer
seconds leave fewer chances to clip or drop out. A take without its log, or one whose stop
failed, counts as the worst for dropouts: unknown is not clean.

## What numbers can and cannot say

Every comparison is made at equal loudness: louder always sounds better, and would hide
everything else. The report says how close the take is to the references' range on each
measurement. It does not say the take sounds better; that is the owner's call, by ear, usually
blind (`redline vocal`, `redline master`).
