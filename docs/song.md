# The song page

One page for one song: the beat, the text, the takes, the mix and the result, driven from a
browser on the same machine. This guide covers the song file, what each station does, the long
jobs and the recording engine behind the page.

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
| `song studio <name> [--port N] [--no-browser]` | the page |

## Stations

The rail across the top lists six stations. A station is lit when the song has what it makes,
the current one is underlined, and a station that does not apply folds away (recording, for a
Suno voice).

| Station | Needs | Does |
|---|---|---|
| Beat | a beat seed or file | an own beat: the beat page mounted inside; its render is the song's beat. A type beat: the file and its tempo. |
| Text | a lyrics seed | pick a version; edit by hand and save as the next version; or the command that asks the writer for a new text. |
| Recording | a rendered beat; a recorded voice | opens the recording engine, records takes, discards the last, calibrates the round trip. |
| Takes | takes | ranks finished takes, plays one over the beat, sets the chosen take. |
| Mix | a chosen voice, references, separated stems | the studio mounted inside; auto mix and its variants. |
| Result | a kept variant or saved settings | renders the final track into `output/<song>/`. |

A hand edit goes through the same checks as the writer's texts: it must read as lyrics, keep the
length and places of the seed's recipe, and copy no lines from the base (the seed's own
`min_words`). Without the copy-check index the save is refused with the command that builds it.
A version file is created only if it does not exist yet, so a hand save and an agent's edit never
overwrite each other.

## Variants

Auto mix searches the vocal level, the compressor threshold and the drive for the settings whose
render measures closest to the references. Its variants move one of those settings one step on
the search grid, chosen by the seed: the same seed gives the same variants. Each variant carries
its distance to the references, measured on the same windows as the search.

The auto result is the closest point the search found, so a variant usually measures further;
the page says so. Variants are for listening: clicking a pad loads its settings into the mix,
"keep" stores them as the song's mix settings, "none" clears the choice. Numbers say closer or
further, never better.

## Jobs

Separation, ranking, auto mix and the result run as subprocesses (`redline song job <kind>
<name>`), each writing one JSON result under `work/songs/<name>/jobs/` through a temporary name.

- One job of a kind runs at a time per song; asking for a second while one runs is refused,
  except ranking, which queues one more run after the current one.
- Cancel stops the process group (`SIGTERM`, then `SIGKILL` after 5 s). The slot stays taken
  until the process has exited.
- Jobs run at lower priority and wait while a take records, so the recording keeps the CPU.
- A finished take starts its ranking. Only takes whose log says they finished, over the song's
  current beat, are ranked; the rest are listed with the reason.

The result folder is built under a temporary name and renamed when complete, so a crash never
leaves a half-written result under the final name.

## Recording

The page never touches the audio: the native engine `redline-rec` owns the mic and the
headphones, as with `redline record` (see [sound.md](sound.md) and RDL-D24).

- One thread owns the engine and the recording session. The page's buttons queue commands to
  it; a 50 ms tick is the only place meters are read, and every open tab polls a copy of that
  snapshot, about 20 times a second while the station is visible.
- Opening goes through stages the page shows: building the engine the first time, launching,
  relaunching at the audio graph's rate, analysing the monitor, a 5 s level check, ready.
- A reply that comes late or does not match its command marks the engine out of sync: commands
  are refused until it is opened again, so a stale reply can never be taken for a new one.
- A beat change while a take records is refused. Closing the tab does not stop a take; stopping
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
