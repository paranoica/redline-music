# Lyrics style profile

`redline lyrics index` writes one profile per artist, per style group, for the scene, for the
owner's own texts and a working profile (the owner's and the scene's, half and half). A profile
holds numbers and single words only: no line and no run of words from any text. The example
next to this file, `lyrics-profile.example.json`, is built from two invented texts.

Section kinds: `intro`, `hook`, `prehook`, `verse`, `bridge`, `outro`, `other`.

A histogram is `{"hist": {value: share}, "p10": n, "p50": n, "p90": n}`.

| Key | What it holds |
|---|---|
| `meta` | `schema_version`, `level` (artist, group, scene, mine, working), `name`, `tracks`, `artists`, `digest` |
| `structure.patterns` | section-kind sequences of a track, joined by `-`, with their shares (top 20) |
| `structure.sections_per_track` | histogram |
| `line_syllables.<kind>` | histogram of syllables per sung line, adlibs left out (30 means 30 or more) |
| `lines_per_section.<kind>` | histogram |
| `rhyme.<kind>.schemes` | shares of four-line windows rhyming AABB, ABAB, ABBA, AAAA or other |
| `rhyme.<kind>.neighbour_share` | share of lines rhyming with one of the next two |
| `hook.per_track` | histogram of hooks per track |
| `hook.repeated_line_share` | share of hook lines repeated inside their hook |
| `adlibs.top` | adlib words with their shares (top 50) |
| `adlibs.line_share`, `adlibs.end_share` | share of lines with an adlib, and with one at the line end |
| `vocabulary.top` | content words (top 300): `per_1000` content words, and the share of the word's uses at a line `start`, `end`, in a `hook`, in a `verse` |
| `latin.top`, `latin.en_share` | the same for words in Latin script, and their share of all words |
| `themes` | empty for now |

Rules behind the numbers:

- Groups and the scene give each artist with 5 or more tracks an equal say; their word lists
  keep only words used by two artists or more.
- Sections that another performer reads count for the scene, not for the host artist.
- A track whose lines are mostly another track's of the same artist is counted once.
- Rhymes: the same stressed vowel; on a last syllable the same closing consonant, else the
  same consonant before the next vowel. Without stress marks the same ending from the
  second-to-last vowel.
