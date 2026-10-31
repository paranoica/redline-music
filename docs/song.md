# The song page

One screen for one song, laid out like a DAW: the beat, the text, the takes, the mix and the
export, driven from a browser on the same machine. This guide covers the song file, the screen,
the long jobs and the recording engine behind it.

## The song file

`redline song new <name>` writes `private/songs/<name>/song.json` (owner-only, like everything
under `private/`):

```json
{
  "format": 1,
  "name": "my-song",
  "beat": {"own": 7},
  "text": {"seed": 12, "version": 3},
  "voice": {"takes": "input/takes/my-song", "chosen": "take-04.wav"},
  "references": ["input/refs/a.mp3", "input/refs/b.mp3"],
  "mix": {"settings": "private/songs/my-song/mix.json", "variant": 1},
  "rev": 9
}
```

- `beat` is `{"own": seed}` (a beat made with `redline beat`) or `{"file": path}` (a type beat).
- `voice` is recorded takes in a folder, with the chosen one, or `{"suno": path}` for a Suno track.
- Paths are relative to the repository and must stay inside `input/`, `private/`, `work/` or
  `output/`. They are checked every time they are used, so a symlink that leads out is refused
  even if it appears later.
- `rev` counts saves. A save names the rev it read; if the file moved on meanwhile (another tab,
  the command line), the save is refused and the page reads the song again.

| Command | |
|---|---|
| `song new <name> --beat-seed N \| --beat FILE [--text-seed N] [--suno FILE] [--ref ...]` | start a song |
| `song refs <name> --ref FILE...` | replace its references |
| `song list`, `song show <name>` | the songs, one song's file |
| `app [--port N] [--no-browser]` | the page, starting at the list of songs |
| `song studio [name] [--port N] [--no-browser]` | the page, with that song open |

## The screen

`redline app` starts at **My songs**. **New song** makes a song from a name, a beat (a type beat
from `input/beats`, or an own seed that has a score: a new own beat is written by the beatmaker
agent, not on the page), a written text or none; the references are every audio file in
`input/refs`. One server holds one open song at a time; opening another closes the first one's
recording session and cancels its jobs, and is refused while a take records.

The song screen:

| Part | What it does |
|---|---|
| Transport | play/pause (Space), stop, record (R), loop (L, or drag on the ruler), Home; the clock in m:ss.s and bar.beat; «дальше:» names the next step; ⋯ holds calibration (cup, cable, speakers), discard the last take, cancel the running job, the result folder; Export |
| Text | the chosen text's lines over the bars they are sung on: two bars a line after a four-bar intro, as the writer fits them. The placement says «примерно» until it is nudged (start bar, one or two bars a line), which is kept in `private/songs/<name>/layout.json` |
| Voice and takes | the chosen take, and every take under it; a star chooses one, ▶ plays one over the beat. Choosing a take starts its preparation (separation and measuring) |
| Beat | an own beat's sections, its 808 notes and drums; a type beat's waveform, or its arrangement once split |
| Editor | for the selected track: the 808 piano roll and the drum grid of an own beat, the voice and beat knobs, the text and its versions |

**Playback.** The main transport always plays the song from files through the studio engine,
so what you hear is what the export renders. Until the chosen take is prepared, the beat and
the raw take play without effects and the knobs wait. The editor's own ▶ loops the selected
section of an own beat live from its score, so a note or a hit is heard at once.

**Own beat edits** are saved to one draft per seed (`private/beats/<seed>/draft.json`) as you
go. The draft becomes the next `score-N.json` and is rendered when you leave the beat's tracks,
press play, record or export. A pattern played by several sections asks once whether to change
it everywhere or only here.

**Knobs and Auto.** The five voice knobs (level, clean, echo, reverb, tune) and the beat knobs
change the mix at once and are saved to the song's mix settings, the file the export renders
with. **Auto** runs one job: separation if the stems are stale, the auto mix, then its result
kept; Ctrl+Z restores the knobs from before. Any change to the beat, the take or the
references marks the mix out of date, and Export runs what is stale first.

## Variants

Auto mix searches the vocal level, the compressor threshold and the drive for the settings whose
render measures closest to the references. Its variants move one of those settings one step on
the search grid, chosen by the seed: the same seed gives the same variants. Each variant carries
its distance to the references, measured on the same windows as the search.

The auto result is the closest point the search found, so a variant usually measures further.
Auto keeps the auto result; the variants stay in `work/songs/<name>/jobs/` for listening.
Numbers say closer or further, never better.

## Jobs

Separation, ranking, auto mix and the result run as subprocesses (`redline song job <kind>
<name>`), each writing one JSON result under `work/songs/<name>/jobs/` through a temporary name.

- One job of a kind runs at a time per song; asking for a second while one runs is refused,
  except ranking, which queues one more run after the current one.
- Cancel stops the process group (`SIGTERM`, then `SIGKILL` after 5 s). The slot stays taken
  until the process has exited.
- Jobs run at lower priority. While a take records, waiting jobs stay queued and running ones
  are paused (`SIGSTOP`), so the recording keeps the CPU; they go on when the take ends.
- A finished take starts its ranking. Only takes whose log says they finished, over the song's
  current beat, are ranked; the rest are listed with the reason.

The result folder is built under a temporary name and renamed when complete, so a crash never
leaves a half-written result under the final name.

## Recording

The page never touches the audio: the native engine `redline-rec` owns the mic and the
headphones, as with `redline record` (see [sound.md](sound.md) and RDL-D24).

- One thread owns the engine and the recording session. The page's buttons queue commands to
  it; a 50 ms tick is the only place meters are read, and every open tab polls a copy of that
  snapshot, about 20 times a second while the engine is open.
- Opening goes through stages the page shows: building the engine the first time, launching,
  relaunching at the audio graph's rate, analysing the monitor, a 5 s level check, ready.
- A reply that comes late or does not match its command marks the engine out of sync: commands
  are refused until it is opened again, so a stale reply can never be taken for a new one.
- The engine follows the song's beat: after a re-render or a new seed it is relaunched on the new
  file, or closed if that beat is not rendered yet. A beat change while a take records is refused. Closing the tab does not stop a take; stopping
  the server (`Ctrl+C`, `SIGTERM`, `SIGHUP`) ends a running take and keeps it, then cancels the
  jobs.
- Takes are created owner-only and never over an existing file.

The JACK path has been checked by tests with a fake engine only; the first sessions on real
hardware are the owner's.

## The server

`redline song studio` starts a local server on `127.0.0.1` with the guards every page here has:
a random token in the address (kept for the tab, dropped from the URL), a host check against
DNS rebinding, JSON bodies with size limits, and a content policy that allows only the page's
own scripts. The beat page's and the studio's routes are mounted under `/api/beat/` and
`/api/mix/`; the studio is built when the song's voice and beat are ready and rebuilt when they
change.
