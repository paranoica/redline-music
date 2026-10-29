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

The render runs in stages: chain, voice, layers, effects, beat, master. Each stage's result is
cached by the settings it depends on (up to 1.5 GB), so moving the master target re-runs only
the master and an effect change does not redo the voice. A newer request cancels an older one
between stages.

A preview renders only the loop, padded on both sides by the longest effect tail, and takes the
last whole render's master gain so that it sounds as loud as the whole would. The whole track
renders on a button.

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

## What numbers can and cannot say

Every comparison is made at equal loudness: louder always sounds better, and would hide
everything else. The report says how close the take is to the references' range on each
measurement. It does not say the take sounds better; that is the owner's call, by ear, usually
blind (`redline vocal`, `redline master`).
