"""The song page's server: one song, its recording, jobs, text, mix and beat on one origin.

The guards are `redline.web.local`'s. The studio and beat routes are the functions of
`redline.studio.server` and `redline.beat.server`, called with the Studio or BeatStudio the
song needs at that moment, read once per request.
"""

import contextlib
import functools
import json
import math
import shlex
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import numpy as np
from scipy.signal import resample_poly

from redline.beat.server import BEAT_MAX_BODY, BeatStudio, beat_get, beat_post
from redline.core.audio import AudioError, Decoded, decode
from redline.core.private import make_private_dir, write_private
from redline.engine.settings import Settings, from_dict, to_dict
from redline.record.process import EngineError
from redline.record.sweep import CalibrationError
from redline.song import model, rec
from redline.song.jobs import Busy, JobRunner, reap_orphans
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

PAGE = "song.html"
NOT_BUILT = "the song page is not built: run `npm --prefix apps/studio run build`"
KINDS = ("separate", "rank", "auto", "result")
MODES = ("cup", "loopback", "speakers")
SNAPSHOT_TTL_S = 0.04
LYRICS = "private/lyrics"
BUILD = "private/lyrics/build"
MAX_SEED = 2**31 - 1
BEAT_GONE = "the beat changed and is not rendered"

REC_ERRORS = (
    (rec.OutOfSync, HTTPStatus.CONFLICT, "out_of_sync"),
    (rec.NotReady, HTTPStatus.CONFLICT, "not_ready"),
    (rec.Busy, HTTPStatus.CONFLICT, "busy"),
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
    """The built song page at `/` and the build's assets; empty while song.html is missing."""
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


def job_argv(kind: str, name: str, seed: int, result: Path) -> list[str]:
    """The command of a job: the same program, its internal `song job`."""
    argv = [sys.executable, "-m", "redline", "song", "job", "--result", str(result)]
    if kind == "auto":
        argv += ["--seed", str(seed)]
    # after "--" a name starting with a dash stays a name
    return [*argv, "--", kind, name]


def _int(value: Any, what: str, low: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{what} is a whole number from {low}")
    return value


def _one(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    return values[0] if len(values) == 1 else None


class SongApp:
    """What the page asks of one song, as route functions over services that tests can swap."""

    def __init__(
        self,
        name: str,
        root: Path | None = None,
        make_rec: Callable[[Callable[[Path], None], Path], RecOwner] | None = None,
        argv: Callable[[str, str, int, Path], list[str]] = job_argv,
        build_studio: Callable[[model.MixInputs, Path, str], Studio] = default_studio,
        make_beat: Callable[..., BeatStudio] = BeatStudio,
        assets: AssetsLoader | None = None,
        kill_after: float = 5.0,
    ) -> None:
        """Serve the song `name`; the services default to the real ones."""
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
        self._save_lock = threading.Lock()
        self._grids: dict[tuple[str, int, int], float | None] = {}
        self._snap: tuple[float, dict[str, Any]] | None = None
        self._snap_lock = threading.Lock()
        make = make_rec or (lambda logged, work: RecOwner(on_take_logged=logged, work=work))
        self.rec = make(self.take_logged, self.work)
        self.jobs = JobRunner(hold=self.rec.taking, kill_after=kill_after)
        reap_orphans(self._jobs_dir())

    # song

    def song(self) -> model.Song:
        """The song as it is on disk now."""
        try:
            return model.load(self.name, self.root)
        except model.SongError as error:
            raise ApiError(HTTPStatus.NOT_FOUND, str(error), "no_song") from None

    def _payload(self, song: model.Song) -> dict[str, Any]:
        return {"song": song.to_json(), "stations": self._stations(song)}

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
        with self._take_gate:
            if self.rec.taking():
                raise ApiError(HTTPStatus.CONFLICT, "a take is running", "take_running")
            if self.song().rev != expected:
                raise self._stale()
            try:
                self._sync_engine(song)
            except rec.Busy:
                # opening or calibrating: the next poll points the engine at the saved beat
                pass
            except rec.RecError as error:
                raise _rec_api_error(error) from None
            except model.SongError as error:
                raise ApiError(HTTPStatus.CONFLICT, str(error), "bad_song") from None
            return self._save(song, expected, kept)

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
        title = title_file.read_text(encoding="utf-8").strip() if title_file.is_file() else ""
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
        self.rec.open(request)
        self._engine_sig = sig

    def _sync_engine(self, song: model.Song | None = None) -> None:
        """Point an open engine at the song's beat as it is now; close it when it is not rendered.

        Args:
            song: The song about to be saved; None reads the saved one, under the lock.

        Raises:
            rec.TakeRunning: A take runs, so the engine stays where it is.
        """
        if self.rec.snapshot().stage in ("idle", "closed", "failed"):
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
        only(body)
        if name == "close":
            self.rec.close()
            return reply_json(HTTPStatus.OK, {"code": "closed", "values": {}})
        outcome = self.rec.toggle() if name == "toggle" else self.rec.discard_last()
        return reply_json(HTTPStatus.OK, {"code": outcome.code, "values": outcome.values})

    # takes and jobs

    def take_logged(self, path: Path) -> None:
        """A take's log is complete: rank the takes, once more after a ranking that runs."""
        with contextlib.suppress(Busy, ApiError):
            self._start("rank", 0, follow_up=True)

    def _start(self, kind: str, seed: int, follow_up: bool = False) -> dict[str, Any]:
        song = self.song()
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
        return self.jobs.start(
            self.name,
            kind,
            self._argv(kind, self.name, seed, result),
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

    def get_jobs(self) -> Reply:
        """The song's jobs and the beat render, as a list."""
        listed = [{"kind": k, **info} for k, info in self.jobs.state(self.name).items()]
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
        if kind not in KINDS:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"kind is one of {', '.join(KINDS)}")
        seed = body.get("seed", 0)
        if _int(seed, "seed") > MAX_SEED:
            raise ApiError(HTTPStatus.BAD_REQUEST, "seed is too large")
        try:
            info = self._start(kind, seed)
        except Busy as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "busy") from None
        return reply_json(HTTPStatus.ACCEPTED, {"kind": kind, **info})

    def cancel_job(self, body: dict) -> Reply:
        """Cancel a job of the song."""
        only(body, "kind")
        kind = body.get("kind")
        if kind not in KINDS:
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
        if method == "GET":
            return studio_get(studio, inner, query)
        reply = studio_post(studio, inner, body)
        if inner == "/api/save" and reply[0] == HTTPStatus.OK:
            self._keep_settings(Path(json.loads(reply[1])["path"]))
        return reply

    def _keep_settings(self, saved: Path) -> None:
        for _ in range(2):
            song = self.song()
            rel = model.relative_to_root(saved, self.root)
            if song.mix["settings"] == rel:
                return
            try:
                model.save(replace(song, mix={**song.mix, "settings": rel}), song.rev, self.root)
                return
            except (model.StaleRev, model.SongError):
                continue

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

    def beat(self, method: str, path: str, query: dict[str, list[str]], body: dict) -> Reply:
        """A beat route for the BeatStudio the song has now."""
        beat = self.beat_studio()
        if method == "GET":
            return beat_get(beat, path, query)
        if path == "/api/beat/render" and self.rec.taking():
            raise ApiError(HTTPStatus.CONFLICT, "a take is running", "take_running")
        reply = beat_post(beat, path, body)
        if path == "/api/beat/render":
            self._snap = None
        return reply

    # dispatch

    def get(self, path: str, query: dict[str, list[str]]) -> Reply:
        """Answer a GET under /api/."""
        try:
            if path == "/api/song":
                return self.get_song()
            if path == "/api/text":
                return self.get_text(query)
            if path == "/api/rec/state":
                return self.rec_state()
            if path == "/api/takes":
                return self.get_takes()
            if path == "/api/takes/audio":
                return self.take_audio(query)
            if path == "/api/jobs":
                return self.get_jobs()
            if path.startswith("/api/mix/"):
                return self.mix("GET", path, query, {})
            if path.startswith("/api/beat/"):
                return self.beat("GET", path, query, {})
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
            if path.startswith("/api/mix/"):
                return self.mix("POST", path, {}, body)
            if path.startswith("/api/beat/"):
                return self.beat("POST", path, {}, body)
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
        beat = urlsplit(self.path).path.startswith("/api/beat/")
        self.max_body = BEAT_MAX_BODY if beat else MAX_BODY
        return super()._body()

    def api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """Every GET of the page."""
        send_reply(self, self.server.app.get(path, query))

    def api_post(self, path: str, body: dict) -> None:
        """Every POST of the page."""
        send_reply(self, self.server.app.post(path, body))


class SongServer(LocalServer):
    """The HTTP server with the song app and its token."""

    def __init__(self, address: tuple[str, int], app: SongApp, token: str, site: Path) -> None:
        """Bind and keep the app and where its page is built."""
        super().__init__(address, Handler, token, song_files(site))
        self.app = app
        self.site = site


def make_server(app: SongApp, token: str, port: int = 0, site: Path | None = None) -> SongServer:
    """A server on 127.0.0.1 only; port 0 takes a free one, site defaults to the studio's build."""
    return SongServer(("127.0.0.1", port), app, token, SITE if site is None else site)


def close_all(httpd: Any, owner: Any, jobs: Any) -> None:
    """Stop recording (a running take is kept), cancel the jobs, then close the server."""
    try:
        owner.shutdown()
    finally:
        try:
            jobs.close()
        finally:
            httpd.server_close()
