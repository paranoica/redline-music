# Flipping a type beat

A type beat is a finished instrumental. A flip keeps what works in it and changes the rest:
the 808 replaced by ours on the same notes, the hats taken out or programmed again, bars moved,
repeated or cut to fit the text, and a few tape and glitch moves on chosen bars. Nothing
overwrites the source; every version is a new file.

## The flow

```sh
uv run redline song new my-song --beat input/beats/beat.mp3
uv run redline song studio my-song        # the beat station opens the flip
```

1. **Разобрать на стемы** (the `flip_stems` job): htdemucs_ft splits the beat into drums, bass,
   other and vocals (about a minute on a GPU), then the grid and the 808 notes are read off the
   stems. The result is cached by the audio's content.
2. **Edit** the draft: it is saved as you go (`draft.json`).
3. **Собрать** (the `flip_render` job): the draft becomes the next `flip-<n>.json` and renders to
   `flip-<n>.wav` with a report.
4. **Сделать битом песни**: the song's beat becomes that render. The page says first how many
   takes were recorded over the beat before: those rank as "another beat" afterwards.

A render used as the song's beat still opens its flip on the original source, so a flip of a flip
never re-separates separated audio.

## What is read off the beat

- **Grid**: the tempo from the beat tracker, refined by a straight-line fit through the low-band
  onsets over the whole file, so the last bar lands where the first does. Half or double tempo
  and the first downbeat are yours to set (½×, 2×, ±10 ms, ±1 beat). Swing is manual.
- **808 notes**: from the bass stem, below 200 Hz. Pitch by pyin from 28 Hz; a note too short for
  the long window is pitched on its steady part or marked unsure. A held 808 whose level ripples
  (drive, the kick beating against it) stays one note; with the grid known, a shallow re-attack
  counts only near a sixteenth. The whole stem's detune is measured and offered as our 808's tune.
  A glide is written as the earlier note overlapping the next, which is how the synth glides.

## What you can change

| Part | Choices |
|---|---|
| 808 | the original stem, ours (the synth's 808 on the notes read, with tune, decay, drive, glide, click, tone), or off |
| Hats | the original, removed (DrumSep's hh, and ride or crash if chosen, subtracted from the drums stem), or ours (8ths, 16ths, a triplet roll at the bar end, a 32nd roll; per block) |
| Levels | drums, other and bass in dB |
| Arrangement | blocks of source bars in play order: move, repeat, split at a bar, change the range, cut; "Предложить под текст" proposes one from the song's text |
| Effects | on play-order bar ranges: reverse (the range turned around), tape stop and tape start (up to 4 s, timed from the range start), stutter and granular (fed by the bars before them in play order) |

Our 808 and hats come in at the level of what they replace. The render is brought toward the
source's loudness and a limiter holds the peaks at -1 dBFS, since it plays as the beat while you
record. A source mastered over full scale is not reached under that ceiling; the report gives the
loudness reached and the source's.

## Proposing an arrangement from the text

The order of sections and their length in bars come from the text, one bar per line. A hook takes
the run of source bars, of its length, that is most alike the other runs of that length (bar
chroma and MFCC), the earliest of equals; every hook reuses it. Verses take the source bars in
order after the intro and loop back when the text is longer than the beat. Intro and outro are 4
bars. The proposal replaces the arrangement after a confirm; change it as you like.

## Files

| Path | What |
|---|---|
| `work/flips/<sha8>/analysis.json` | grid, bars, notes, detune |
| `work/flips/<sha8>/stems48/` | the four stems at 48 kHz (about 300 MB for a 4-minute beat) |
| `work/flips/<sha8>/draft.json` | the page's working copy |
| `work/flips/<sha8>/flip-<n>.json`, `.wav`, `.report.md` | each version, never overwritten (a 4-minute render is about 90 MB) |

`redline flip clean <source>` removes old renders but the newest and the one a song uses.

## Models and licences

The separation models run locally; every model file is pinned by its sha256 and checked before
it loads. Their weights carry their authors' terms and are not part of this repository.

Editing a beat does not change its licence. Free type beats are often for non-profit use only;
check the producer's terms before releasing anything made from one.
