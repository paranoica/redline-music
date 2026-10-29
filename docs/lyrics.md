# Lyrics

How Redline writes texts in the style of the scene without copying it. Commands are in the
[README](../README.md); the profile's fields are in [lyrics-profile.md](lyrics-profile.md).

## The base

The base is a private folder of texts (`private/lyrics/`, ignored by git): other artists' tracks
grouped by artist, and the owner's own. It is never part of the repository.

`redline lyrics index` builds two things from it into `private/lyrics/build/`:

- **style profiles**: numbers and single words only, per artist, per style group, for the scene
  and for the owner. Section patterns, syllables per line, rhyme schemes, hook repeats, adlibs,
  the scene's vocabulary;
- **copy-check digests** of every run of words, to refuse any draft that repeats eight or more
  words in a row from any text, generated texts included.

## Writing a text

```sh
uv run redline lyrics write --seed 53 --beat input/beats/beat.mp3
```

The seed fixes a **recipe** drawn from the profile: the sections and their order, lines per
section, syllables per line, rhyme schemes, a topic as vibe words, adlibs, the share of English
words, and devices the text must use. The recipe is frozen in the seed's folder, so a seed is the
same text task every time.

With `--beat` the recipe fits the beat:

- the tempo is measured at half time (60-120 BPM), so one line is one bar;
- the bars cap the number of lines;
- the beat's brightness sets the delivery, `hard` or `calm` (`--delivery` overrides);
- the flow of each section (sixteenths, triplets, eighths, chant) sets its syllables.

The command writes a request file for the writer agent (`.claude/agents/lyrics-writer.md`) and
exits with code 3. Once the draft is back, the same command checks it:

- it must be lyrics, of the recipe's length and line form;
- ordinary places (a kitchen, a district) only as often as the recipe's device asks;
- no copy from the base or from other generated texts.

A failed draft goes back with the reasons, up to four rounds. A saved text is `text.txt` in
`private/lyrics/generated/<seed>/`.

## Editing

```sh
uv run redline lyrics edit --seed 53 "make the hook harder"
```

The writer gets the current text and the change, and keeps every other line word for word.
The same checks apply, except the line form, which an edit may change on purpose. Each saved
edit is the next version: `text-2.txt`, `text-3.txt`.

## Marks

```sh
uv run redline lyrics marks
```

A local page lists every saved text and version. Mark a line **cringe** or **good** with a click
or a key (`C`, `N`, `0` to clear, arrows to move). A line repeated in a hook carries one mark.
Marks are kept per version in `marks-<version>.json` next to the text.

Every new request, for a new text or an edit, carries the newest 15 cringe lines and 10 good
lines across all seeds: the cringe ones as what to stay away from, the good ones as the level to
hold, never to be repeated.

## Suno

```sh
uv run redline lyrics suno --seed 53
```

Prints and saves the card: a title, the style tags (from the delivery and the flows, or from
`private/lyrics/suno-style.txt`), the exclusions, the sliders, and the lyrics with Suno's section
tags. English acronyms are spelled out the way they are read; a shouted four-letter word stays a
word. `--version` picks an older version.

In Suno: upload the beat, choose **Edit vocals**, paste the style, the exclusions and the lyrics.
Roll several takes; the beat holds the rhythm, the takes differ in voice and delivery.
