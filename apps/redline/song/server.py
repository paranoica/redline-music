"""The song page's server: the active song, its recording, jobs, text, mix and beat on one origin.

The guards are `redline.web.local`'s. The studio and beat routes are the functions of
`redline.studio.server` and `redline.beat.server`, called with the Studio or BeatStudio the
song needs at that moment, read once per request.
"""

import contextlib
import fcntl
import functools
import json
import math
import shlex
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO
from urllib.parse import parse_qs, urlsplit

import numpy as np
from scipy.signal import resample_poly

from redline.beat.server import (
    BEAT_MAX_BODY,
    DRAFT,
    BeatStudio,
    Refused,
    StaleBase,
    beat_get,
    beat_post,
)
from redline.core.audio import AudioError, Decoded, decode
from redline.core.private import make_private_dir, write_private
from redline.engine.settings import Settings, from_dict, to_dict
from redline.record.process import EngineError
from redline.record.sweep import CalibrationError
from redline.song import model, rec
from redline.song.jobs import ACTIVE, CANCELLED, DONE, FAILED, Busy, JobRunner, reap_orphans
from redline.song.rec import OpenRequest, RecOwner
from redline.studio.server import (
    PAGE_KINDS,
    POLICY,
    SITE,
    Reply,
    Studio,
    reply_bytes,
    reply_json,
    send_reply,
    studio_get,
    studio_post,
    wav_bytes,
)
from redline.web.local import MAX_BODY, BadRequest, LocalHandler, LocalServer, only

if TYPE_CHECKING:
    from redline.song.app import AppHost

PAGE = "daw.html"
NOT_BUILT = "the song page is not built: run `npm --prefix apps/studio run build`"
KINDS = ("separate", "rank", "auto", "result")
FLIP_KINDS = ("flip_stems", "flip_render")
MODES = ("cup", "loopback", "speakers")
SNAPSHOT_TTL_S = 0.04
LYRICS = "private/lyrics"
BUILD = "private/lyrics/build"
MAX_SEED = 2**31 - 1
BEAT_GONE = "the beat changed and is not rendered"
PEAKS = (200, 2000, 8000)
LAST_BAR = 511
BARS_PER_LINE = (1, 2)
LAYOUT = ("start_bar", "bars_per_line")
STEP_TICK_S = 0.05
MIX_CLOSE_TIMEOUT_S = 10.0

REC_ERRORS = (
    (rec.OutOfSync, HTTPStatus.CONFLICT, "out_of_sync"),
    (rec.NotReady, HTTPStatus.CONFLICT, "not_ready"),
    (rec.Busy, HTTPStatus.CONFLICT, "engine_busy"),
    (rec.TakeRunning, HTTPStatus.CONFLICT, "take_running"),
    (rec.RecClosed, HTTPStatus.CONFLICT, "closed"),
    (rec.CommandTimeout, HTTPStatus.GATEWAY_TIMEOUT, "timeout"),
)

AssetsLoader = Callable[[], tuple[Any, Any, Any]]


def _rec_api_error(error: Exception) -> "ApiError":
    for kind, status, code in REC_ERRORS:
        if isinstance(error, kind):
            return ApiError(status, str(error), code)
    return ApiError(HTTPStatus.BAD_GATEWAY, str(error), "engine")


class ApiError(BadRequest):
    """A refusal with a code the page reads, and any more fields it carries."""

    def __init__(self, status: int, message: str, code: str | None = None, **extra: Any) -> None:
        """Keep the status, the code and the extra fields for the answer."""
        super().__init__(HTTPStatus(status), message)
        self.payload = {"error": message, **({"code": code} if code else {}), **extra}


def song_files(root: Path) -> dict[str, tuple[Path, str]]:
    """The built song screen at `/` and the build's assets; empty while daw.html is missing."""
    if not (root / PAGE).is_file():
        return {}
    files = {
        "/" + path.relative_to(root).as_posix(): (path, PAGE_KINDS[path.suffix])
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in PAGE_KINDS and path.suffix != ".html"
    }
    files["/"] = (root / PAGE, PAGE_KINDS[".html"])
    return files


class CachedOnly:
    """A separator that never separates: the stems must already be in the cache."""

    def __init__(self) -> None:
        """Take the cache identity of the real separator, so its entries are found."""
        from redline.measure.separate import RoformerSeparator

        self.id = RoformerSeparator().id

    def __call__(self, samples: Any, rate: int) -> Any:
        """Refuse: the separate job has not filled the cache for these files."""
        from redline.measure.separate import SeparationError

        raise SeparationError("the stems are not cached: run the separation first")


def default_studio(inputs: model.MixInputs, work: Path, name: str) -> Studio:
    """The Studio of a measured voice and beat, from cached stems only."""
    from redline.engine import live
    from redline.engine.track import track_engine
    from redline.measure.run import measure_inputs, measure_own

    refs, separator = list(inputs.refs), CachedOnly()
    if inputs.beat is None:
        measured = measure_inputs(inputs.voice, refs, work, separator)
    else:
        measured = measure_own(inputs.voice, inputs.beat, refs, work, separator)
    engine = track_engine(measured)
    live.check_vocal(engine)
    references = []
    for path in refs:
        decoded = decode(path)
        references.append((path.stem, decoded.samples, decoded.rate))
    stems = measured.track_stems
    return Studio(engine, stems.vocal + stems.beat, work / "studio", name, references)


def default_assets(lyrics: Path, build: Path) -> tuple[Any, Any, Any]:
    """The copy-check index, the profile and the stress marker; None for what cannot load."""
    from redline.lyrics.base.fingerprint import FingerprintIndex
    from redline.lyrics.base.profile import load_profile
    from redline.lyrics.text.parse import LyricsError

    name = "target" if (build / "profiles" / "target.json").exists() else "working"
    try:
        index = FingerprintIndex.load(build)
        profile = load_profile(build / "profiles" / f"{name}.json")
    except (LyricsError, OSError, ValueError):
        return None, None, str
    if not profile["meta"].get("stress"):
        return index, profile, str

    @functools.cache
    def stresser() -> Callable[[str], str]:
        from redline.lyrics.text import stress

        return stress.load_stresser()

    @functools.cache
    def mark(text: str) -> str:
        return stresser()(text)

    return index, profile, mark


# the beat sits under the voice when a take is auditioned
BEAT_UNDER_DB = -6.0


def _over_beat(take: np.ndarray, beat: Decoded, rate: int) -> np.ndarray:
    """The take with the beat under it from frame 0, as the engine lined them up."""
    under = beat.samples
    if beat.rate != rate:
        divisor = math.gcd(rate, beat.rate)
        under = resample_poly(under, rate // divisor, beat.rate // divisor, axis=0)
    mixed = take.copy()
    count = min(len(take), len(under))
    mixed[:count] += under[:count] * 10 ** (BEAT_UNDER_DB / 20)
    peak = float(np.max(np.abs(mixed)))
    return mixed / peak * 0.99 if peak > 0.99 else mixed


def job_argv(kind: str, name: str, seed: int, result: Path, flip: int | None = None) -> list[str]:
    """The command of a job: the same program, its internal `song job`."""
    argv = [sys.executable, "-m", "redline", "song", "job", "--result", str(result)]
    if kind == "auto":
        argv += ["--seed", str(seed)]
    if flip is not None:
        if isinstance(flip, bool) or not isinstance(flip, int) or flip < 1:
            raise ValueError(f"flip is a whole number from 1, not {flip!r}")
        argv += ["--flip", str(flip)]
    # after "--" a name starting with a dash stays a name
    return [*argv, "--", kind, name]


def _int(value: Any, what: str, low: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{what} is a whole number from {low}")
    return value


def _one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if len(values) == 1 else None


def min_max(mono: np.ndarray, points: int) -> list[list[float]]:
    """[min, max] over `points` equal spans of a mono signal; fewer only for a shorter one."""
    count = min(points, len(mono))
    if count == 0:
        return []
    starts = np.linspace(0, len(mono), count + 1).astype(np.int64)[:-1]
    low, high = np.minimum.reduceat(mono, starts), np.maximum.reduceat(mono, starts)
    return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(low, high, strict=True)]


@dataclass
class MixChain:
    """The "mix" job: separate when stale, auto, then keep variant 0, one step at a time."""

    seed: int
    state: str = "running"
    step: str | None = None
    reason: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancelled: bool = False

    def info(self) -> dict[str, Any]:
        """The chain as a job of the list."""
        return {
            "state": self.state,
            "step": self.step,
            "paused": False,
            "reason": self.reason,
            "result": None,
            "log": None,
            "follow_up": False,
            "pid": None,
            "started": self.started,
            "finished": self.finished,
        }

    def end(self, state: str, reason: str | None = None) -> None:
        """Finish the chain in a state."""
        self.state, self.reason, self.finished = state, reason, time.time()


class SongApp:
    """What the page asks of one song, as route functions over services that tests can swap."""

    def __init__(
        self,
        name: str,
        rec: RecOwner,
        jobs: JobRunner,
        root: Path | None = None,
        argv: Callable[..., list[str]] = job_argv,
        build_studio: Callable[[model.MixInputs, Path, str], Studio] = default_studio,
        make_beat: Callable[..., BeatStudio] = BeatStudio,
        assets: AssetsLoader | None = None,
    ) -> None:
        """Serve the song `name` over the process's recorder and job runner.

        The recorder and the runner stay the caller's: `close` lets go of what this song holds
        in them, never ends them. The other services default to the real ones.
        """
        self.name = name
        self.root = (root or model.repo_root()).resolve()
        self.work = model.resolve_inside("work", self.root)
        self._argv = argv
        self._build_studio = build_studio
        self._make_beat = make_beat
        self._assets_loader = assets or functools.partial(
            default_assets,
            model.resolve_inside(LYRICS, self.root),
            model.resolve_inside(BUILD, self.root),
        )
        self._assets: tuple[Any, Any, Any] | None = None
        self._text_lock = threading.Lock()
        self._studio: Studio | None = None
        self._studio_key: str | None = None
        self._studio_lock = threading.Lock()
        self._beat: BeatStudio | None = None
        self._beat_lock = threading.Lock()
        self._engine_lock = threading.Lock()
        self._engine_sig: tuple[str, int, int] | None = None
        self._take_gate = threading.Lock()
        self._save_lock = threading.RLock()
        self._grids: dict[tuple[str, int, int], float | None] = {}
        self._snap: tuple[float, dict[str, Any]] | None = None
        self._snap_lock = threading.Lock()
        self.rec = rec
        self.jobs = jobs
        self._rec_used = False
        self._closed = False
        self._mix: MixChain | None = None
        self._mix_thread: threading.Thread | None = None
        self._mix_lock = threading.Lock()
        self._hashes: dict[tuple[str, int, int], str] = {}
        self._peaks_lock = threading.Lock()
        self._server_lock = self._claim_song()
        reap_orphans(self._jobs_dir())
        from redline.flip.server import FlipRoutes

        self.flip = FlipRoutes(self)

    # song

    def song(self) -> model.Song:
        """The song as it is on disk now."""
        try:
            return model.load(self.name, self.root)
        except model.SongError as error:
            raise ApiError(HTTPStatus.NOT_FOUND, str(error), "no_song") from None

    def _payload(self, song: model.Song) -> dict[str, Any]:
        return {
            "song": song.to_json(),
            "stations": self._stations(song),
            "facts": self._facts(song),
        }

    def _state_dir(self) -> Path:
        return make_private_dir(self.work / "songs" / self.name, self.root)

    def _seed_folder(self, song: model.Song) -> Path | None:
        if song.text is None:
            return None
        lyrics = model.resolve_inside(LYRICS, self.root)
        return lyrics / "generated" / str(song.text["seed"])

    def _beat_rel(self, song: model.Song) -> str:
        if "own" in song.beat:
            return f"work/beats/{song.beat['own']}/beat.wav"
        return song.beat["file"]

    def _stations(self, song: model.Song) -> dict[str, Any]:
        return {
            "beat": self._beat_station(song),
            "text": self._text_station(song),
            "voice": self._voice_station(song),
            "mix": self._mix_station(song),
            "result": {"folder": self._result_folder()},
        }

    def _beat_station(self, song: model.Song) -> dict[str, Any]:
        try:
            path = model.beat_file(song, self.root)
        except model.SongError:
            path = None
        rel = self._beat_rel(song)
        if "own" in song.beat:
            seed = song.beat["own"]
            return {
                "kind": "own",
                "seed": seed,
                "bpm": self._score_bpm(seed),
                "file": rel,
                "rendered": path is not None and path.is_file(),
            }
        return {"kind": "file", "file": rel, "bpm": self._file_bpm(path)}

    def _score_bpm(self, seed: int) -> float | None:
        from redline.beat.render import RenderError, latest_score

        try:
            folder = model.resolve_inside("private/beats", self.root) / str(seed)
            return float(json.loads(latest_score(folder).read_text(encoding="utf-8"))["bpm"])
        except (RenderError, OSError, ValueError, KeyError, TypeError):
            return None

    def _file_bpm(self, path: Path | None) -> float | None:
        if path is None or not path.is_file():
            return None
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in self._grids:
            from redline.takes.rank import beat_grid

            try:
                decoded = decode(path)
                self._grids[key] = beat_grid(decoded.samples.mean(axis=1), decoded.rate).bpm
            except (AudioError, ValueError, RuntimeError):
                self._grids[key] = None
        return self._grids[key]

    def _text_station(self, song: model.Song) -> dict[str, Any]:
        beat = self._beat_rel(song)
        if song.text is None:
            return {
                "seed": None,
                "command": f"uv run redline lyrics write --seed <N> --beat {shlex.quote(beat)}",
            }
        from redline.lyrics.write.generate import versions

        seed = song.text["seed"]
        folder = self._seed_folder(song)
        found = sorted(versions(folder)) if folder is not None and folder.is_dir() else []
        return {
            "seed": seed,
            "version": song.text["version"],
            "versions": found,
            "pending": self._pending(folder),
            "command": shlex.join(
                ["uv", "run", "redline", "lyrics", "write", "--seed", str(seed), "--beat", beat]
            ),
        }

    def _pending(self, folder: Path | None) -> str | None:
        if folder is None or not folder.is_dir():
            return None
        try:
            edits = json.loads((folder / "edits.json").read_text(encoding="utf-8"))
            pending = edits.get("pending")
            if isinstance(pending, dict):
                return f"edit-{pending['version']}/request-{pending['round']}.md"
        except (OSError, ValueError, KeyError, AttributeError):
            pass
        try:
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(state, dict) and state.get("status") != "saved":
            asked = sorted(p.name for p in folder.glob("request-*.md"))
            return asked[-1] if asked else None
        return None

    def _voice_station(self, song: model.Song) -> dict[str, Any]:
        if "suno" in song.voice:
            return {
                "kind": "suno",
                "file": song.voice["suno"],
                "separated": self._stems_ready(song),
            }
        folder = model.resolve_inside(song.voice["takes"], self.root, "voice.takes")
        names = (
            sorted(p.name for p in folder.iterdir() if model.TAKE_FILE.fullmatch(p.name))
            if folder.is_dir()
            else []
        )
        return {
            "kind": "takes",
            "folder": song.voice["takes"],
            "takes": names,
            "chosen": song.voice["chosen"],
        }

    def _result_folder(self) -> str | None:
        try:
            out = model.resolve_inside(f"output/{self.name}", self.root)
        except model.SongError:
            return None
        if not out.is_dir():
            return None
        runs = sorted(p.name for p in out.iterdir() if p.is_dir() and not p.name.startswith("."))
        return f"output/{self.name}/{runs[-1]}" if runs else None

    def _claim_song(self) -> TextIO:
        """Hold the song for this server's life: a second one would take its jobs for orphans."""
        held = (self._state_dir() / "server.lock").open("a", encoding="utf-8")
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            held.close()
            raise model.SongError(f"{self.name}: already open in another song studio") from None
        return held

    def _jobs_dir(self) -> Path:
        return self._state_dir() / "jobs"

    def _result_path(self, kind: str) -> Path:
        return self._jobs_dir() / f"{kind}.json"

    def _stems_ready(self, song: model.Song) -> bool:
        try:
            inputs = model.mix_inputs(song, self.root)
            marker = json.loads(self._result_path("separate").read_text(encoding="utf-8"))
            return marker.get("key") == inputs.key()
        except (model.SongError, OSError, ValueError, AttributeError):
            return False

    def _mix_station(self, song: model.Song) -> dict[str, Any]:
        try:
            model.mix_inputs(song, self.root)
            reason = None if self._stems_ready(song) else "stems not ready"
        except model.NoVoice as error:
            reason = str(error)
        return {"ready": reason is None, "reason": reason, "variant": song.mix["variant"]}

    def _facts(self, song: model.Song) -> dict[str, Any]:
        exporting = self.jobs.state(self.name).get("result", {}).get("state") in ACTIVE
        return {
            "beat_audio": self._beat_file_ready(song),
            "beat_analysed": self._beat_analysed(song),
            "studio_ready": self._studio_ready(song),
            "mix_stale": self._mix_stale(song),
            "refs": len(song.references),
            "voice_kind": "suno" if "suno" in song.voice else "own",
            "exporting": exporting,
            "beat_dirty": self._beat_dirty(song),
        }

    def _beat_file_ready(self, song: model.Song) -> bool:
        try:
            return model.beat_file(song, self.root).is_file()
        except model.SongError:
            return False

    def _beat_analysed(self, song: model.Song) -> bool:
        if "own" in song.beat:
            return True
        from redline.flip import pipeline

        try:
            line = pipeline.lineage(song.beat["file"], self.root)
            return pipeline.read_analysis(pipeline.flip_dir(self.root, line.sha8)) is not None
        except pipeline.PipelineError:
            return False

    def _studio_ready(self, song: model.Song) -> bool:
        try:
            model.mix_inputs(song, self.root)
        except model.SongError:
            return False
        return self._stems_ready(song)

    def _mix_key_file(self) -> Path:
        return self._state_dir() / "mix-key.json"

    def _record_mix_key(self, key: str | None, settings: str) -> None:
        if key is not None:
            data = {"key": key, "settings": settings}
            write_private(self._mix_key_file(), json.dumps(data) + "\n")

    def _mix_stale(self, song: model.Song) -> bool:
        """No kept settings, or settings kept for other voice, beat or reference files."""
        rel = song.mix["settings"]
        try:
            current = model.mix_inputs(song, self.root).key()
            if rel is None or not model.resolve_inside(rel, self.root).is_file():
                return True
            record = json.loads(self._mix_key_file().read_text(encoding="utf-8"))
        except (model.SongError, OSError, ValueError):
            return True
        return not (
            isinstance(record, dict)
            and record.get("key") == current
            and record.get("settings") == rel
        )

    def _beat_dirty(self, song: model.Song) -> bool:
        """An own beat with a draft over its newest score, or a newest score not rendered.

        A flip render is dirty when the flip's draft differs from the version it was made from.
        """
        if "own" not in song.beat:
            return self._flip_dirty(song)
        from redline.beat.render import RenderError, latest_score

        seed = song.beat["own"]
        folder = model.resolve_inside("private/beats", self.root) / str(seed)
        try:
            newest = latest_score(folder).name
        except RenderError:
            return False
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            if json.loads((folder / DRAFT).read_text(encoding="utf-8"))["base"] == newest:
                return True
        out = self.work / "beats" / str(seed)
        try:
            rendered = json.loads((out / "render.json").read_text(encoding="utf-8"))["score"]
        except (OSError, ValueError, TypeError, KeyError):
            return True
        return rendered != newest or not (out / "beat.wav").is_file()

    def _flip_dirty(self, song: model.Song) -> bool:
        from redline.flip import model as flip_model
        from redline.flip import pipeline

        found = pipeline.FLIP_RENDER.fullmatch(song.beat.get("file", ""))
        if found is None:
            return self._source_flip_dirty(song)
        try:
            folder = pipeline.flip_dir(self.root, found.group(1))
            draft = flip_model.load(pipeline.inside(folder, flip_model.DRAFT))
            used = flip_model.load(pipeline.inside(folder, f"flip-{found.group(2)}.json"))
        except (flip_model.FlipError, pipeline.PipelineError):
            return False
        return draft.to_json() != used.to_json()

    def _source_flip_dirty(self, song: model.Song) -> bool:
        """An analysed source file whose draft differs from the one the analysis wrote."""
        from redline.flip import model as flip_model
        from redline.flip import pipeline

        beat = song.beat.get("file")
        if not isinstance(beat, str):
            return False
        try:
            path = model.resolve_inside(beat, self.root)
            folder = pipeline.flip_dir(self.root, pipeline.source_sha8(path))
            analysis = pipeline.read_analysis(folder)
            if analysis is None:
                return False
            draft = flip_model.load(pipeline.inside(folder, flip_model.DRAFT)).to_json()
            default = pipeline._default_draft(analysis["source"], analysis).to_json()
        except (model.SongError, flip_model.FlipError, pipeline.PipelineError, KeyError):
            return False
        # the grid is the analysis's own and does not change the sound of the source
        return {**draft, "grid": None} != {**default, "grid": None}

    def get_song(self) -> Reply:
        """The song and its stations."""
        return reply_json(HTTPStatus.OK, self._payload(self.song()))

    def post_song(self, body: dict) -> Reply:
        """Change the song under the rev the page read; 412 when it moved on."""
        only(body, "rev", "set")
        expected = _int(body.get("rev"), "rev")
        changes = body.get("set")
        if not isinstance(changes, dict) or not changes:
            raise ApiError(HTTPStatus.BAD_REQUEST, "set is an object with the changes")
        only(changes, "text", "voice_chosen", "beat", "mix_variant")
        song = self.song()
        moved_beat = False
        kept: str | None = None
        if "text" in changes:
            song = replace(song, text=self._new_text(changes["text"]))
        if "voice_chosen" in changes:
            song = replace(song, voice=self._new_voice(song, changes["voice_chosen"]))
        if "beat" in changes:
            song, moved_beat = replace(song, beat=self._new_beat(changes["beat"])), True
        if "mix_variant" in changes:
            song, kept = self._new_variant(song, changes["mix_variant"])
        if not moved_beat:
            return self._save(song, expected, kept)
        return self._save_beat_change(song, expected, kept)

    def _save_beat_change(self, song: model.Song, expected: int, kept: str | None) -> Reply:
        # under the gate no take starts, so the engine can follow once the song is saved;
        # an engine opening or calibrating, or any refusal, is left to the next poll
        with self._take_gate:
            if self.rec.taking():
                raise ApiError(HTTPStatus.CONFLICT, "a take is running", "take_running")
            reply = self._save(song, expected, kept)
            self._follow_beat()
            return reply

    def _stale(self) -> ApiError:
        return ApiError(
            HTTPStatus.PRECONDITION_FAILED,
            "the song changed: read it again",
            "stale",
            **self._payload(self.song()),
        )

    def _save(self, song: model.Song, expected: int, kept: str | None) -> Reply:
        # the song and the settings files of a kept variant change together, never interleaved
        with self._save_lock:
            return self._save_locked(song, expected, kept)

    def _save_locked(self, song: model.Song, expected: int, kept: str | None) -> Reply:
        try:
            saved = model.save(song, expected, self.root)
        except model.StaleRev:
            raise self._stale() from None
        if kept is not None:
            make_private_dir(model.song_dir(self.name, self.root), self.root)
            write_private(model.song_dir(self.name, self.root) / "mix.json", kept)
            # the Studio opens from the settings saved under its track key
            studio_folder = make_private_dir(self.work / "studio" / self.name, self.root)
            write_private(studio_folder / "settings.json", kept)
            with contextlib.suppress(model.SongError, OSError):
                self._record_mix_key(
                    model.mix_inputs(saved, self.root).key(), saved.mix["settings"]
                )
        return reply_json(HTTPStatus.OK, self._payload(saved))

    def _new_text(self, value: Any) -> dict[str, int] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "text is {seed, version} or null")
        only(value, "seed", "version")
        seed = _int(value.get("seed"), "seed")
        from redline.lyrics.write.generate import versions

        folder = model.resolve_inside(LYRICS, self.root) / "generated" / str(seed)
        have = versions(folder) if folder.is_dir() else {}
        # a seed alone takes its latest version
        if value.get("version") is None:
            version = max(have, default=0)
        else:
            version = _int(value.get("version"), "version", 1)
        if version not in have:
            raise ApiError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                f"seed {seed} has no version {version}",
                "no_text",
                errors=[
                    {"kind": "no text", "place": f"{seed}/{version}", "message": "no such text"}
                ],
            )
        return {"seed": seed, "version": version}

    def _new_voice(self, song: model.Song, chosen: Any) -> dict[str, Any]:
        if "takes" not in song.voice:
            raise ApiError(HTTPStatus.BAD_REQUEST, "a Suno voice has no takes to choose")
        if chosen is not None:
            try:
                model.take_file(song, chosen, self.root)
            except model.SongError as error:
                raise ApiError(HTTPStatus.NOT_FOUND, str(error), "no_take") from None
        return {**song.voice, "chosen": chosen}

    def _new_beat(self, value: Any) -> dict[str, Any]:
        try:
            return model._parse_beat(value)
        except model.SongError as error:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(error)) from None

    def _new_variant(self, song: model.Song, slot: Any) -> tuple[model.Song, str | None]:
        if slot is None:
            return replace(song, mix={**song.mix, "variant": None}), None
        slot = _int(slot, "mix_variant")
        try:
            data = self._variants_data(song)
        except ApiError as error:
            if error.payload.get("code") != "no_variants":
                raise
            raise ApiError(
                HTTPStatus.UNPROCESSABLE_ENTITY, f"there is no variant {slot}", "no_variant"
            ) from None
        try:
            chosen = data["auto"] if slot == 0 else data["variants"][slot - 1]["settings"]
            known = to_dict(Settings())
            settings = from_dict({k: v for k, v in chosen.items() if k in known})
        except (ValueError, KeyError, IndexError, TypeError):
            raise ApiError(
                HTTPStatus.UNPROCESSABLE_ENTITY, f"there is no variant {slot}", "no_variant"
            ) from None
        text = json.dumps(to_dict(settings), indent=1) + "\n"
        rel = f"private/songs/{self.name}/mix.json"
        return replace(song, mix={"settings": rel, "variant": slot}), text

    # text

    def get_text(self, query: dict[str, list[str]]) -> Reply:
        """A saved text of the song's seed, with its title."""
        song = self.song()
        folder = self._seed_folder(song)
        if song.text is None or folder is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "the song has no text", "no_text")
        asked = _one(query, "version")
        if asked is not None and not (asked.isascii() and asked.isdigit()):
            raise ApiError(HTTPStatus.BAD_REQUEST, "version is a whole number")
        version = int(asked) if asked is not None else song.text["version"]
        from redline.lyrics.write.generate import versions

        have = versions(folder) if folder.is_dir() else {}
        if version not in have:
            raise ApiError(HTTPStatus.NOT_FOUND, f"no version {version}", "no_text")
        title_file = folder / ("title.txt" if version == 1 else f"title-{version}.txt")
        title = (
            title_file.read_text(encoding="utf-8", errors="replace").strip()
            if title_file.is_file()
            else ""
        )
        text = have[version].read_text(encoding="utf-8")
        return reply_json(
            HTTPStatus.OK,
            {"seed": song.text["seed"], "version": version, "title": title, "text": text},
        )

    def _text_assets(self) -> tuple[Any, Any, Any]:
        if self._assets is None:
            loaded = self._assets_loader()
            if loaded[0] is None or loaded[1] is None:
                # not kept: building the index must show without a restart
                return loaded
            self._assets = loaded
        return self._assets

    def save_text(self, body: dict) -> Reply:
        """Save a hand edit as the next version, after the Editor's checks."""
        only(body, "seed", "base", "text")
        seed, base = _int(body.get("seed"), "seed"), _int(body.get("base"), "base", 1)
        text = body.get("text")
        if not isinstance(text, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "text is a string")
        song = self.song()
        folder = self._seed_folder(song)
        if song.text is None or folder is None or song.text["seed"] != seed:
            raise ApiError(HTTPStatus.BAD_REQUEST, "seed is not the song's text seed")
        from redline.song.text import save_hand_edit

        with self._text_lock:
            index, profile, mark = self._text_assets()
            done = save_hand_edit(
                folder,
                base,
                text,
                index=index,
                profile=profile,
                mark=mark,
                lyrics=Path(LYRICS),
                build=Path(BUILD),
            )
        if not done.saved:
            errors = [{"kind": e.kind, "place": e.place, "message": e.message} for e in done.errors]
            first = errors[0]["message"] if errors else "refused"
            return reply_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": first, "code": "refused", "errors": errors},
            )
        return reply_json(HTTPStatus.OK, {"version": done.version})

    # recording

    def _snapshot(self) -> dict[str, Any]:
        with self._snap_lock:
            now = time.monotonic()
            if self._snap is not None and now - self._snap[0] < SNAPSHOT_TTL_S:
                return self._snap[1]
            data = _snapshot_json(self.rec.snapshot())
            self._snap = (now, data)
            return data

    def rec_state(self) -> Reply:
        """The recording snapshot, shared by pollers within one sampler tick."""
        self._follow_beat()
        return reply_json(HTTPStatus.OK, self._snapshot())

    def _open_request(self, song: model.Song) -> OpenRequest:
        if "takes" not in song.voice:
            raise ApiError(HTTPStatus.CONFLICT, "a Suno voice is not recorded", "no_recording")
        beat = model.beat_file(song, self.root)
        if not beat.is_file():
            raise ApiError(HTTPStatus.CONFLICT, f"the beat is not rendered: {beat.name}", "no_beat")
        folder = model.resolve_inside(song.voice["takes"], self.root, "voice.takes")
        make_private_dir(folder, self.root)
        settings = None
        if song.mix["settings"] is not None:
            path = model.resolve_inside(song.mix["settings"], self.root, "mix.settings")
            if path.is_file():
                from redline.engine import settings as chain

                try:
                    settings = chain.load(path)
                except (ValueError, OSError):
                    settings = None
        refs = tuple(model.resolve_inside(r, self.root, "references") for r in song.references)
        separator = None
        if refs:
            from redline.measure.separate import RoformerSeparator

            separator = RoformerSeparator()
        return OpenRequest(beat, folder, self.work, refs, separator, settings)

    def _beat_sig(self, song: model.Song) -> tuple[str, int, int] | None:
        try:
            path = model.beat_file(song, self.root)
            stat = path.stat()
        except (model.SongError, OSError):
            return None
        return (str(path), stat.st_mtime_ns, stat.st_size)

    def _launch_engine(self, song: model.Song) -> None:
        request = self._open_request(song)
        sig = self._beat_sig(song)
        self._rec_used = True
        self.rec.open(request)
        self._engine_sig = sig

    def _sync_engine(self, song: model.Song | None = None) -> None:
        """Point an open engine at the song's beat as it is now; close it when it is not rendered.

        Args:
            song: The song about to be saved; None reads the saved one, under the lock.

        Raises:
            rec.TakeRunning: A take runs, so the engine stays where it is.
        """
        if self.rec.stage() in ("idle", "closed", "failed"):
            return
        with self._engine_lock:
            song = self.song() if song is None else song
            sig = self._beat_sig(song)
            if sig == self._engine_sig:
                return
            # checked before any open is tried: a refused open still blocks the stop meanwhile
            if self.rec.taking():
                raise rec.TakeRunning("a take is running")
            if sig is None:
                self.rec.close(reason=BEAT_GONE)
                self._engine_sig = None
                return
            self._launch_engine(song)

    def _follow_beat(self) -> None:
        with contextlib.suppress(rec.RecError, ApiError, model.SongError):
            self._sync_engine()

    def rec_command(self, name: str, body: dict) -> Reply:
        """open, toggle, discard, calibrate or close; the errors are 409 with a code."""
        self._snap = None
        try:
            if name in ("toggle", "calibrate"):
                self._follow_beat()
            if name == "toggle":
                with self._take_gate:
                    return self._rec_run(name, body)
            return self._rec_run(name, body)
        except rec.RecError as error:
            raise _rec_api_error(error) from None
        except (EngineError, CalibrationError) as error:
            raise ApiError(HTTPStatus.BAD_GATEWAY, str(error), "engine") from None

    def _rec_run(self, name: str, body: dict) -> Reply:
        if name == "open":
            only(body)
            with self._engine_lock:
                self._launch_engine(self.song())
            return reply_json(HTTPStatus.ACCEPTED, {"code": "opening", "values": {}})
        if name == "calibrate":
            only(body, "mode")
            mode = body.get("mode")
            if mode not in MODES:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"mode is one of {', '.join(MODES)}")
            done = self.rec.calibrate(mode)
            values = {
                "frames": done.frames,
                "ms": done.ms,
                "peak_to_noise_db": done.peak_to_noise_db,
                "mode": done.mode,
                "notices": [{"code": n.code, "values": n.values} for n in done.notices],
            }
            return reply_json(HTTPStatus.OK, {"code": "calibrated", "values": values})
        if name == "toggle":
            only(body, "take")
            take = body.get("take")
            if "take" in body and (not isinstance(take, str) or not take):
                raise ApiError(HTTPStatus.BAD_REQUEST, "take is a take's name")
            outcome = self.rec.toggle() if take is None else self.rec.toggle(expect=take)
            return reply_json(HTTPStatus.OK, {"code": outcome.code, "values": outcome.values})
        only(body)
        if name == "close":
            self.rec.close()
            return reply_json(HTTPStatus.OK, {"code": "closed", "values": {}})
        outcome = self.rec.discard_last()
        return reply_json(HTTPStatus.OK, {"code": outcome.code, "values": outcome.values})

    # takes and jobs

    def take_logged(self, path: Path) -> None:
        """A take's log is complete: rank the takes, once more after a ranking that runs."""
        with contextlib.suppress(Busy, ApiError):
            self._start("rank", 0, follow_up=True)

    def _start(
        self, kind: str, seed: int, follow_up: bool = False, flip: int | None = None
    ) -> dict[str, Any]:
        song = self.song()
        if kind in FLIP_KINDS:
            from redline.flip import pipeline

            ctx = self.flip.context()
            if kind == "flip_render" and flip not in pipeline.versions(ctx.folder):
                raise ApiError(HTTPStatus.NOT_FOUND, f"no flip {flip}", "no_flip")
        if kind == "rank" and "takes" not in song.voice:
            raise ApiError(HTTPStatus.CONFLICT, "a Suno voice has no takes", "no_takes")
        if kind in ("separate", "auto", "result"):
            try:
                model.mix_inputs(song, self.root)
            except model.NoVoice as error:
                raise ApiError(HTTPStatus.CONFLICT, str(error), "no_voice") from None
        self._state_dir()
        make_private_dir(self._jobs_dir(), self.root)
        result = self._result_path(kind)
        argv = (
            self._argv(kind, self.name, seed, result)
            if flip is None
            else self._argv(kind, self.name, seed, result, flip)
        )
        return self.jobs.start(
            self.name,
            kind,
            argv,
            result,
            heavy=True,
            follow_up=follow_up,
        )

    def get_takes(self) -> Reply:
        """The latest ranking and the ranking job."""
        ranking = None
        try:
            path = self._state_dir() / "ranking.json"
            ranking = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        return reply_json(
            HTTPStatus.OK, {"ranking": ranking, "job": self.jobs.state(self.name).get("rank")}
        )

    def take_audio(self, query: dict[str, list[str]]) -> Reply:
        """A take as a 16-bit WAV; the name must be a take of the song's folder."""
        name = _one(query, "name")
        song = self.song()
        try:
            path = model.take_file(song, name or "", self.root)
        except model.SongError as error:
            raise ApiError(HTTPStatus.NOT_FOUND, str(error), "no_take") from None
        over = _one(query, "with")
        if over not in (None, "beat"):
            raise ApiError(HTTPStatus.BAD_REQUEST, "with is beat or nothing")
        try:
            decoded = decode(path)
            samples = decoded.samples
            if over == "beat":
                samples = _over_beat(
                    samples, decode(model.beat_file(song, self.root)), decoded.rate
                )
        except (AudioError, model.SongError) as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "unreadable") from None
        return reply_bytes(HTTPStatus.OK, wav_bytes(samples, decoded.rate), "audio/wav")

    def _beat_path(self, song: model.Song) -> Path:
        try:
            path = model.beat_file(song, self.root)
        except model.SongError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "unreadable") from None
        if path.is_file():
            return path
        if "own" in song.beat:
            raise ApiError(HTTPStatus.CONFLICT, "the beat is not rendered yet", "no_render")
        raise ApiError(HTTPStatus.CONFLICT, f"{path.name}: no such file", "no_beat")

    def beat_audio(self) -> Reply:
        """The song's beat as a 16-bit stereo WAV at its own rate."""
        path = self._beat_path(self.song())
        try:
            decoded = decode(path)
        except AudioError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "unreadable") from None
        return reply_bytes(HTTPStatus.OK, wav_bytes(decoded.samples, decoded.rate), "audio/wav")

    def _take_path(self, song: model.Song, name: str | None) -> Path:
        if name is None or not model.TAKE_FILE.fullmatch(name):
            raise ApiError(HTTPStatus.BAD_REQUEST, "name is a take file, take-NN.wav")
        if "takes" not in song.voice:
            raise ApiError(HTTPStatus.CONFLICT, "a Suno voice has no takes", "no_takes")
        try:
            folder = model.resolve_inside(song.voice["takes"], self.root, "voice.takes")
        except model.SongError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "outside") from None
        path = folder / name
        if not path.exists():
            raise ApiError(HTTPStatus.NOT_FOUND, f"{name}: no such take", "no_take")
        if path.resolve().parent != folder:
            raise ApiError(HTTPStatus.CONFLICT, f"{name} resolves out of its folder", "outside")
        return path

    def _hash(self, path: Path) -> str:
        from redline.record.takes import file_sha256

        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in self._hashes:
            self._hashes[key] = file_sha256(path)
        return self._hashes[key]

    def _peaks_of(self, path: Path, points: int) -> dict[str, Any]:
        try:
            digest = self._hash(path)
        except OSError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "unreadable") from None
        with self._peaks_lock:
            cache = (
                make_private_dir(self._state_dir() / "peaks", self.root) / f"{digest}-{points}.json"
            )
            with contextlib.suppress(OSError, ValueError):
                data = json.loads(cache.read_text(encoding="utf-8"))
                if isinstance(data, dict) and {"duration_s", "rate", "peaks"} <= set(data):
                    return data
            try:
                decoded = decode(path)
            except AudioError as error:
                raise ApiError(HTTPStatus.CONFLICT, str(error), "unreadable") from None
            mono = decoded.samples.mean(axis=1)
            data = {
                "duration_s": len(mono) / decoded.rate,
                "rate": decoded.rate,
                "peaks": min_max(mono, points),
            }
            write_private(cache, json.dumps(data))
            return data

    def peaks(self, query: dict[str, list[str]]) -> Reply:
        """[min, max] pairs of the beat or a take, mono, cached by the file's hash."""
        low, default, high = PEAKS
        raw = _one(query, "n")
        if raw is None and "n" in query:
            raise ApiError(HTTPStatus.BAD_REQUEST, "give one n")
        if raw is not None and not (raw.isascii() and raw.isdigit() and len(raw) <= 9):
            raise ApiError(HTTPStatus.BAD_REQUEST, "n is a whole number")
        points = default if raw is None else min(max(int(raw), low), high)
        of = _one(query, "of")
        song = self.song()
        if of == "beat":
            if "name" in query:
                raise ApiError(HTTPStatus.BAD_REQUEST, "the beat takes no name")
            path = self._beat_path(song)
        elif of == "take":
            path = self._take_path(song, _one(query, "name"))
        else:
            raise ApiError(HTTPStatus.BAD_REQUEST, "of is beat or take")
        return reply_json(HTTPStatus.OK, self._peaks_of(path, points))

    def _layout_file(self) -> Path:
        return model.song_dir(self.name, self.root) / "layout.json"

    @staticmethod
    def _layout(body: Any) -> dict[str, int | None]:
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "the layout is an object")
        only(body, *LAYOUT)
        missing = [key for key in LAYOUT if key not in body]
        if missing:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{', '.join(missing)} missing: give it or null")
        start, per = body["start_bar"], body["bars_per_line"]
        if start is not None and (
            isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= LAST_BAR
        ):
            raise ApiError(HTTPStatus.BAD_REQUEST, f"start_bar is 0 to {LAST_BAR} or null")
        if per is not None and (isinstance(per, bool) or per not in BARS_PER_LINE):
            raise ApiError(HTTPStatus.BAD_REQUEST, "bars_per_line is 1, 2 or null")
        return {"start_bar": start, "bars_per_line": per}

    def get_layout(self) -> Reply:
        """Where the lyric lines start and how many bars each takes; null where not nudged."""
        try:
            data = self._layout(json.loads(self._layout_file().read_text(encoding="utf-8")))
        except (OSError, ValueError, ApiError, BadRequest):
            data = {"start_bar": None, "bars_per_line": None}
        return reply_json(HTTPStatus.OK, data)

    def post_layout(self, body: dict) -> Reply:
        """Keep the lyric layout after its values are checked."""
        data = self._layout(body)
        make_private_dir(model.song_dir(self.name, self.root), self.root)
        write_private(self._layout_file(), json.dumps(data) + "\n")
        return reply_json(HTTPStatus.OK, data)

    def _mix_info(self, chain: MixChain) -> dict[str, Any]:
        info = chain.info()
        if chain.state == "running" and chain.step in ("separate", "auto"):
            step = self.jobs.state(self.name).get("mix")
            if step is not None and step["state"] in ACTIVE:
                for key in ("state", "paused", "pid", "log"):
                    info[key] = step[key]
        return info

    def _mix_running(self) -> bool:
        return self._mix is not None and self._mix.state == "running"

    def _start_mix(self, seed: int) -> dict[str, Any]:
        song = self.song()
        try:
            model.mix_inputs(song, self.root)
        except model.NoVoice as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "no_voice") from None
        with self._mix_lock:
            running = self.jobs.state(self.name)
            if self._mix_running() or any(
                running.get(kind, {}).get("state") in ACTIVE for kind in ("separate", "auto")
            ):
                raise ApiError(HTTPStatus.CONFLICT, "a mix step is already running", "busy")
            steps = ("auto", "keep") if self._stems_ready(song) else ("separate", "auto", "keep")
            chain = self._mix = MixChain(seed)
            self._mix_thread = threading.Thread(
                target=self._run_mix, args=(chain, steps), daemon=True
            )
            self._mix_thread.start()
        return self._mix_info(chain)

    def _run_mix(self, chain: MixChain, steps: tuple[str, ...]) -> None:
        for step in steps:
            with self._mix_lock:
                if chain.cancelled or self._closed:
                    chain.end(CANCELLED)
                    return
                chain.step = step
                if step != "keep":
                    try:
                        make_private_dir(self._jobs_dir(), self.root)
                        result = self._result_path(step)
                        argv = self._argv(step, self.name, chain.seed, result)
                        self.jobs.start(self.name, "mix", argv, result, heavy=True)
                    except (Busy, ApiError, model.SongError, OSError, ValueError) as error:
                        chain.end(FAILED, str(error))
                        return
            if step == "keep":
                break
            done = self._wait_step()
            with self._mix_lock:
                if done["state"] == CANCELLED:
                    chain.end(CANCELLED)
                    return
                if done["state"] != DONE:
                    chain.end(FAILED, done.get("reason") or f"{step} did not finish")
                    return
        self._finish_mix(chain)

    def _finish_mix(self, chain: MixChain) -> None:
        # under the lock a cancel or a close waits for the keep instead of racing its writes
        with self._mix_lock:
            if chain.cancelled or self._closed:
                chain.end(CANCELLED)
                return
            try:
                self._keep_auto()
            except (ApiError, model.SongError, OSError) as error:
                chain.end(FAILED, str(error))
            else:
                chain.end(DONE)

    def _wait_step(self) -> dict[str, Any]:
        while True:
            info = self.jobs.state(self.name).get("mix")
            if info is None:
                return {"state": FAILED, "reason": "the step is gone"}
            if info["state"] not in ACTIVE:
                return info
            time.sleep(STEP_TICK_S)

    def _keep_auto(self) -> None:
        """Keep variant 0 as a POST /api/song does, read again while the song moves on."""
        for _ in range(3):
            song = self.song()
            changed, kept = self._new_variant(song, 0)
            try:
                self._save(changed, song.rev, kept)
                return
            except ApiError as error:
                if error.payload.get("code") != "stale":
                    raise
        raise ApiError(HTTPStatus.CONFLICT, "the song kept changing: run the mix again", "stale")

    def _cancel_mix(self) -> Reply:
        with self._mix_lock:
            chain = self._mix
            if chain is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "no mix job", "no_job")
            if chain.state == "running":
                chain.cancelled = True
                if chain.step in ("separate", "auto"):
                    self.jobs.cancel(self.name, "mix")
            return reply_json(HTTPStatus.OK, {"kind": "mix", **self._mix_info(chain)})

    def get_jobs(self) -> Reply:
        """The song's jobs and the beat render, as a list."""
        listed = [
            {"kind": k, **info} for k, info in self.jobs.state(self.name).items() if k != "mix"
        ]
        chain = self._mix
        if chain is not None:
            listed.append({"kind": "mix", **self._mix_info(chain)})
        beat = self._beat
        if beat is not None:
            status = beat.render_status()
            if status["state"] != "idle":
                listed.append(
                    {
                        "kind": "beat_render",
                        "state": status["state"],
                        "reason": status["error"],
                        "started": None,
                        "finished": None,
                    }
                )
        return reply_json(HTTPStatus.OK, {"jobs": listed})

    def start_job(self, body: dict) -> Reply:
        """Start a job; 409 when the song has one of that kind unfinished."""
        only(body, "kind", "seed")
        kind = body.get("kind")
        startable = (*KINDS, "flip_stems", "mix")
        if kind not in startable:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"kind is one of {', '.join(startable)}")
        seed = body.get("seed", 0)
        if _int(seed, "seed") > MAX_SEED:
            raise ApiError(HTTPStatus.BAD_REQUEST, "seed is too large")
        if kind == "mix":
            return reply_json(HTTPStatus.ACCEPTED, {"kind": kind, **self._start_mix(seed)})
        # the chain writes the same stems marker and variants: its check and this start are one
        mixing = kind in ("separate", "auto")
        with self._mix_lock if mixing else contextlib.nullcontext():
            if mixing and self._mix_running():
                raise ApiError(HTTPStatus.CONFLICT, "the mix job is running", "busy")
            try:
                info = self._start(kind, seed)
            except Busy as error:
                raise ApiError(HTTPStatus.CONFLICT, str(error), "busy") from None
        return reply_json(HTTPStatus.ACCEPTED, {"kind": kind, **info})

    def cancel_job(self, body: dict) -> Reply:
        """Cancel a job of the song."""
        only(body, "kind")
        kind = body.get("kind")
        if kind == "mix":
            return self._cancel_mix()
        if kind not in (*KINDS, *FLIP_KINDS):
            raise ApiError(HTTPStatus.BAD_REQUEST, f"kind is one of {', '.join(KINDS)}")
        info = self.jobs.cancel(self.name, kind)
        if info is None:
            raise ApiError(HTTPStatus.NOT_FOUND, f"no {kind} job", "no_job")
        return reply_json(HTTPStatus.OK, {"kind": kind, **info})

    # mix

    def _no_voice(self, reason: str) -> ApiError:
        return ApiError(HTTPStatus.CONFLICT, reason, "no_voice", reason=reason)

    def studio(self) -> Studio:
        """The Studio for the song's voice and beat now, built or swapped as needed.

        Raises:
            ApiError: 409 no_voice while it cannot be built, 409 busy while the one in use renders.
        """
        with self._studio_lock:
            song = self.song()
            try:
                inputs = model.mix_inputs(song, self.root)
            except model.NoVoice as error:
                raise self._no_voice(str(error)) from None
            key = inputs.key()
            if self._studio is not None and self._studio_key == key:
                return self._studio
            if not self._stems_ready(song):
                raise self._no_voice("stems not ready")
            old = self._studio
            if old is not None:
                if old._render_lock.locked() or old._preview_lock.locked():
                    raise ApiError(HTTPStatus.CONFLICT, "a render is running", "busy")
                with old._seq_lock:
                    old.latest_seq = sys.maxsize
                self._studio = self._studio_key = None
            try:
                built = self._build_studio(inputs, self.work, self.name)
            except (ValueError, OSError, AudioError, RuntimeError, ImportError) as error:
                raise self._no_voice(str(error)) from None
            except Exception as error:  # a missing stem cache is a SeparationError  # noqa: BLE001
                raise self._no_voice(str(error)) from None
            self._studio, self._studio_key = built, key
            return built

    def mix(self, method: str, path: str, query: dict[str, list[str]], body: dict) -> Reply:
        """A studio route for the Studio the song has now; the Studio is read once."""
        inner = "/api/" + path.removeprefix("/api/mix/")
        if method == "GET" and inner == "/api/variants":
            return self._variants()
        studio = self.studio()
        key = self._studio_key
        if method == "GET":
            return studio_get(studio, inner, query)
        if inner != "/api/save":
            return studio_post(studio, inner, body)
        # the studio's save and a kept variant write the same settings files
        with self._save_lock:
            reply = studio_post(studio, inner, body)
            if reply[0] == HTTPStatus.OK:
                self._keep_settings(Path(json.loads(reply[1])["path"]), key)
            return reply

    def _keep_settings(self, saved: Path, key: str | None) -> None:
        """Copy the studio's save to the song's settings file, the one the result job reads."""
        folder = make_private_dir(model.song_dir(self.name, self.root), self.root)
        write_private(folder / "mix.json", saved.read_text(encoding="utf-8"))
        rel = f"private/songs/{self.name}/mix.json"
        for _ in range(3):
            song = self.song()
            if song.mix["settings"] == rel:
                break
            try:
                model.save(replace(song, mix={**song.mix, "settings": rel}), song.rev, self.root)
                break
            except model.StaleRev:
                continue
        else:
            raise ApiError(
                HTTPStatus.CONFLICT, "the song changed while saving: save again", "stale"
            )
        self._record_mix_key(key, rel)

    def _variants_data(self, song: model.Song) -> dict[str, Any]:
        try:
            data = json.loads((self._state_dir() / "variants.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ApiError(HTTPStatus.NOT_FOUND, "no auto mix has run yet", "no_variants") from None
        try:
            current = model.mix_inputs(song, self.root).key()
        except (model.SongError, OSError):
            current = None
        if not isinstance(data, dict) or current is None or data.get("key") != current:
            raise ApiError(
                HTTPStatus.CONFLICT,
                "the variants are of another mix: run auto again",
                "stale_variants",
            )
        return data

    def _variants(self) -> Reply:
        return reply_json(HTTPStatus.OK, self._variants_data(self.song()))

    # beat

    def beat_studio(self) -> BeatStudio:
        """The BeatStudio of the song's own seed, swapped when the seed changed.

        Raises:
            ApiError: 409 while the beat is a file, or while the current seed renders.
        """
        with self._beat_lock:
            song = self.song()
            if "own" not in song.beat:
                raise ApiError(HTTPStatus.CONFLICT, "the song's beat is a file", "no_beat_seed")
            seed = song.beat["own"]
            if self._beat is not None and self._beat.seed == seed:
                return self._beat
            if self._beat is not None and self._beat.render_status()["state"] == "running":
                raise ApiError(HTTPStatus.CONFLICT, "a render is running", "busy")
            root = model.resolve_inside("private/beats", self.root)
            self._beat = self._make_beat(seed, root, self.work)
            return self._beat

    def beat_draft(self, method: str, body: dict) -> Reply:
        """The own beat's working copy: read it, or replace it after the player parses it."""
        if "own" not in self.song().beat:
            raise ApiError(HTTPStatus.CONFLICT, "the song's beat is not an own seed", "not_own")
        beat = self.beat_studio()
        if method == "GET":
            score, base = beat.draft()
            return reply_json(HTTPStatus.OK, {"score": score, "base": base})
        only(body, "score", "base")
        score, base = body.get("score"), body.get("base")
        if not isinstance(score, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "score is a JSON object")
        if not isinstance(base, str):
            raise ApiError(HTTPStatus.BAD_REQUEST, "base is a saved score's name")
        try:
            beat.save_draft(score, base)
        except StaleBase as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "stale_base") from None
        except Refused as refused:
            raise ApiError(
                refused.status, str(refused), "refused", errors=list(refused.errors)
            ) from None
        return reply_json(HTTPStatus.OK, {"ok": True})

    def beat(self, method: str, path: str, query: dict[str, list[str]], body: dict) -> Reply:
        """A beat route for the BeatStudio the song has now."""
        if path == "/api/beat/draft":
            return self.beat_draft(method, body)
        beat = self.beat_studio()
        if method == "GET":
            return beat_get(beat, path, query)
        if path == "/api/beat/render" and self.rec.taking():
            raise ApiError(HTTPStatus.CONFLICT, "a take is running", "take_running")
        reply = beat_post(beat, path, body)
        if path == "/api/beat/render":
            self._snap = None
        return reply

    # closing

    def close(self) -> None:
        """Let the song go: its recording session, its jobs and mix chain, its studios, its lock.

        A mix chain writes nothing once this returns; it is waited for at most
        MIX_CLOSE_TIMEOUT_S.

        Raises:
            rec.RecError: The recording session did not close; nothing was let go.
        """
        if self._closed:
            return
        if self._rec_used:
            with contextlib.suppress(rec.RecClosed):
                self.rec.close()
            self._engine_sig = None
        self._closed = True
        with self._mix_lock:
            if self._mix is not None:
                self._mix.cancelled = True
            chain_thread = self._mix_thread
        self.jobs.cancel_song(self.name)
        if chain_thread is not None and chain_thread is not threading.current_thread():
            chain_thread.join(MIX_CLOSE_TIMEOUT_S)
        with self._studio_lock:
            self._studio = self._studio_key = None
        with self._beat_lock:
            self._beat = None
        self._server_lock.close()

    # dispatch

    def _area_get(self, path: str, query: dict[str, list[str]]) -> Reply:
        if path.startswith("/api/mix/"):
            return self.mix("GET", path, query, {})
        if path.startswith("/api/beat/"):
            return self.beat("GET", path, query, {})
        return self.flip.get(path, query)

    def _area_post(self, path: str, body: dict) -> Reply:
        if path.startswith("/api/mix/"):
            return self.mix("POST", path, {}, body)
        if path.startswith("/api/beat/"):
            return self.beat("POST", path, {}, body)
        return self.flip.post(path, body)

    def get(self, path: str, query: dict[str, list[str]]) -> Reply:
        """Answer a GET under /api/."""
        plain: dict[str, Callable[[], Reply]] = {
            "/api/song": self.get_song,
            "/api/rec/state": self.rec_state,
            "/api/takes": self.get_takes,
            "/api/jobs": self.get_jobs,
            "/api/song/beat-audio": self.beat_audio,
            "/api/song/layout": self.get_layout,
        }
        asked: dict[str, Callable[[dict[str, list[str]]], Reply]] = {
            "/api/text": self.get_text,
            "/api/takes/audio": self.take_audio,
            "/api/peaks": self.peaks,
        }
        try:
            if path in plain:
                return plain[path]()
            if path in asked:
                return asked[path](query)
            if path.startswith(("/api/mix/", "/api/beat/", "/api/flip/")):
                return self._area_get(path, query)
        except ApiError as error:
            return reply_json(error.status, error.payload)
        return reply_json(HTTPStatus.NOT_FOUND, {"error": "no such call"})

    def post(self, path: str, body: dict) -> Reply:
        """Answer a POST."""
        try:
            if path == "/api/song":
                return self.post_song(body)
            if path == "/api/text/save":
                return self.save_text(body)
            if path.startswith("/api/rec/"):
                name = path.removeprefix("/api/rec/")
                if name in ("open", "toggle", "discard", "calibrate", "close"):
                    return self.rec_command(name, body)
            if path == "/api/jobs/start":
                return self.start_job(body)
            if path == "/api/jobs/cancel":
                return self.cancel_job(body)
            if path == "/api/song/layout":
                return self.post_layout(body)
            if path.startswith(("/api/mix/", "/api/beat/", "/api/flip/")):
                return self._area_post(path, body)
        except ApiError as error:
            return reply_json(error.status, error.payload)
        return reply_json(HTTPStatus.NOT_FOUND, {"error": "no such call"})


def _snapshot_json(snap: rec.RecSnapshot) -> dict[str, Any]:
    peak = snap.mic_peak
    peak_db = 20 * math.log10(peak) if peak > 0 else None
    trip = snap.round_trip
    stage = "closed" if snap.stage == "idle" else snap.stage
    return {
        "stage": stage,
        "error": snap.error,
        "out_of_sync": snap.out_of_sync,
        "recording": snap.recording,
        "stopping": snap.stopping,
        "take": f"{snap.take}.wav" if snap.take else None,
        "seconds": snap.take_seconds,
        "peak_db": peak_db,
        "clip": peak >= 0.999,
        "dropouts": snap.dropouts,
        "monitor_ms": snap.monitor_ms,
        "round_trip": None if trip is None else {"label": trip[0], "source": trip[1]},
        "rate": snap.rate,
        "quantum": snap.quantum,
        "dropped_effects": list(snap.dropped),
        "notices": [{"code": c, "values": v} for c, v in snap.notices],
        "age_ms": snap.age_s * 1000,
    }


class Handler(LocalHandler):
    """Answers the song page; the app and the token sit on the server."""

    server: "SongServer"  # pyright: ignore[reportIncompatibleVariableOverride]

    def send(self, status: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        """Answer with the guard headers and the studio's page policy on."""
        self.answered = True
        super().send(status, body, kind, {"Content-Security-Policy": POLICY, **(extra or {})})

    def _guarded(self, call: Callable[[], None]) -> None:
        self.answered = False
        try:
            call()
        except Exception as error:  # noqa: BLE001
            # the page shows a JSON error; a dropped connection tells it nothing
            if not self.answered:
                self.error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")

    def do_GET(self) -> None:
        """The page once it is built, a note until then, and the API."""
        url = urlsplit(self.path)
        given = parse_qs(url.query).get("t", [None])[0]
        if (
            url.path in ("/", "/index.html")
            and "/" not in self.server.files
            and self._host_ok()
            and self._token_ok(given)
        ):
            # built after the server started: picked up on reload
            self.server.files = song_files(self.server.site)
            if "/" not in self.server.files:
                body = NOT_BUILT.encode()
                return self.send(HTTPStatus.SERVICE_UNAVAILABLE, body, "text/plain; charset=utf-8")
        self._guarded(super().do_GET)

    def do_POST(self) -> None:
        """The API calls that change something."""
        self._guarded(super().do_POST)

    def _body(self) -> dict:
        # a score or a long flip with its notes and drum rows outgrows the plain limit
        big = urlsplit(self.path).path.startswith(("/api/beat/", "/api/flip/"))
        self.max_body = BEAT_MAX_BODY if big else MAX_BODY
        return super()._body()

    def api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """Every GET of the page."""
        send_reply(self, self.server.host.get(path, query))

    def api_post(self, path: str, body: dict) -> None:
        """Every POST of the page."""
        send_reply(self, self.server.host.post(path, body))


class SongServer(LocalServer):
    """The HTTP server with the songs' host and its token."""

    def __init__(self, address: tuple[str, int], host: "AppHost", token: str, site: Path) -> None:
        """Bind and keep the host and where its page is built."""
        super().__init__(address, Handler, token, song_files(site))
        self.host = host
        self.site = site

    @property
    def app(self) -> SongApp | None:
        """The active song's app, None while no song is open."""
        return self.host.active


def make_server(host: "AppHost", token: str, port: int = 0, site: Path | None = None) -> SongServer:
    """A server on 127.0.0.1 only; port 0 takes a free one, site defaults to the studio's build."""
    return SongServer(("127.0.0.1", port), host, token, SITE if site is None else site)


def close_all(httpd: Any, owner: Any, jobs: Any) -> None:
    """Stop recording (a running take is kept), cancel the jobs, then close the server."""
    try:
        owner.shutdown()
    finally:
        try:
            jobs.close()
        finally:
            httpd.server_close()
