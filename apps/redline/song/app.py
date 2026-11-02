"""One server for every song: the list, a new song, and the one song open at a time.

The recorder and the job runner live as long as the process; a song's app is opened over them
and closed when another song is opened. A switch waits for the requests already inside the
old app, so none of them runs on a closed one.
"""

import contextlib
import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from redline.song import model, rec
from redline.song.jobs import JobRunner
from redline.song.rec import RecOwner
from redline.song.server import (
    MAX_SEED,
    ApiError,
    Reply,
    SongApp,
    _rec_api_error,
    reply_json,
)
from redline.web.local import only

AUDIO = (".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff")
BEATS = "input/beats"
REFS = "input/refs"
SWITCH_TIMEOUT_S = 30.0
ENGINE_BUSY = (*rec.OPENING, "calibrating")

AppFactory = Callable[..., SongApp]
RecFactory = Callable[[Callable[[Path], None], Path], RecOwner]


class Gate:
    """Requests share the active app; a switch waits until it is alone and holds new ones back."""

    def __init__(self) -> None:
        """No request inside, no switch waiting."""
        self._cond = threading.Condition()
        self._readers = 0
        self._writing = False
        self._waiting = 0

    @contextlib.contextmanager
    def shared(self) -> Iterator[None]:
        """Hold the active app for one request."""
        with self._cond:
            self._cond.wait_for(lambda: not self._writing and not self._waiting)
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                self._cond.notify_all()

    @contextlib.contextmanager
    def alone(self, timeout: float) -> Iterator[None]:
        """Hold the app with no request inside.

        Raises:
            TimeoutError: The requests inside did not finish in time.
        """
        with self._cond:
            self._waiting += 1
            try:
                free = self._cond.wait_for(lambda: not self._writing and not self._readers, timeout)
            finally:
                self._waiting -= 1
                self._cond.notify_all()
            if not free:
                raise TimeoutError("requests of the open song are still running")
            self._writing = True
        try:
            yield
        finally:
            with self._cond:
                self._writing = False
                self._cond.notify_all()


def audio_in(relative: str, root: Path) -> list[str]:
    """The audio files directly in a folder, stored relative to the root and sorted."""
    folder = model.resolve_inside(relative, root)
    if not folder.is_dir():
        return []
    return sorted(
        f"{relative}/{path.name}"
        for path in folder.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in AUDIO
    )


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")


def _beat_kind(beat: dict[str, Any], root: Path) -> tuple[str, str]:
    from redline.flip import pipeline

    if "own" in beat:
        return "own", f"seed {beat['own']}"
    found = pipeline.FLIP_RENDER.fullmatch(beat["file"])
    if found is None:
        return "file", Path(beat["file"]).name
    sha8, n = found.group(1), found.group(2)
    try:
        folder = pipeline.flip_dir(root, sha8)
        data = json.loads(pipeline.inside(folder, f"flip-{n}.json").read_text(encoding="utf-8"))
        source = data["source"]
        if not isinstance(source, str):
            raise TypeError(source)
    except (pipeline.PipelineError, OSError, ValueError, KeyError, TypeError):
        return "flip", f"flip {n}"
    return "flip", f"{Path(source).name}, flip {n}"


def song_summary(song: model.Song, root: Path) -> dict[str, Any]:
    """A line of the song list: beat kind, takes, text, and when it last changed."""
    stamps = [(model.song_dir(song.name, root) / "song.json").stat().st_mtime]
    takes = 0
    if "takes" in song.voice:
        try:
            folder = model.resolve_inside(song.voice["takes"], root, "voice.takes")
            found = [p for p in folder.iterdir() if model.TAKE_FILE.fullmatch(p.name)]
        except (model.SongError, OSError):
            found = []
        takes = len(found)
        stamps += [p.stat().st_mtime for p in found]
    kind, label = _beat_kind(song.beat, root)
    return {
        "name": song.name,
        "beat": kind,
        "beat_label": label,
        "takes": takes,
        "text": song.text is not None,
        "modified": _iso(max(stamps)),
    }


def score_seeds(root: Path) -> list[int]:
    """The own beat seeds with a saved score."""
    from redline.beat.render import SAVED

    folder = model.resolve_inside("private/beats", root)
    if not folder.is_dir():
        return []
    seeds = []
    for path in folder.iterdir():
        numbered = path.is_dir() and path.name.isascii() and path.name.isdigit()
        if numbered and any(SAVED.fullmatch(p.name) for p in path.iterdir()):
            seeds.append(int(path.name))
    return sorted(seeds)


def written_texts(root: Path) -> list[dict[str, Any]]:
    """The written lyric seeds with the title of their latest version; never the text."""
    from redline.lyrics.write.generate import versions

    folder = model.resolve_inside("private/lyrics/generated", root)
    if not folder.is_dir():
        return []
    texts = []
    for path in folder.iterdir():
        if not (path.is_dir() and path.name.isascii() and path.name.isdigit()):
            continue
        have = versions(path)
        if not have:
            continue
        version = max(have)
        title_file = path / ("title.txt" if version == 1 else f"title-{version}.txt")
        title = (
            title_file.read_text(encoding="utf-8", errors="replace").strip()
            if title_file.is_file()
            else ""
        )
        texts.append({"seed": int(path.name), "title": title})
    return sorted(texts, key=lambda t: t["seed"])


def _unprocessable(message: str) -> ApiError:
    return ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, message, "bad_input")


def _seed(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SEED:
        raise _unprocessable(f"{what} is a whole number from 0 to {MAX_SEED}")
    return value


class AppHost:
    """The process's recorder and jobs, and the one song app the page talks to."""

    def __init__(
        self,
        root: Path | None = None,
        make_rec: RecFactory | None = None,
        make_app: AppFactory = SongApp,
        kill_after: float = 5.0,
        switch_timeout: float = SWITCH_TIMEOUT_S,
    ) -> None:
        """Start the recorder and the job runner; no song is open yet.

        Args:
            root: The repository; tests point it elsewhere.
            make_rec: Builds the recorder from the take-logged callback and the work folder.
            make_app: Builds a song's app from its name, the recorder, the runner and the root.
            kill_after: Seconds between SIGTERM and SIGKILL when a job is cancelled.
            switch_timeout: How long a switch waits for the requests of the open song.
        """
        self.root = (root or model.repo_root()).resolve()
        work = model.resolve_inside("work", self.root)
        make = make_rec or (lambda logged, folder: RecOwner(on_take_logged=logged, work=folder))
        self.rec = make(self.take_logged, work)
        self.jobs = JobRunner(hold=self.rec.taking, kill_after=kill_after)
        self._make_app = make_app
        self._switch_timeout = switch_timeout
        self._active: SongApp | None = None
        self._lock = threading.Lock()
        self._gate = Gate()

    @property
    def active(self) -> SongApp | None:
        """The open song's app."""
        with self._lock:
            return self._active

    def take_logged(self, path: Path) -> None:
        """A take's log is complete: the song recording it ranks its takes.

        Only the open song has a recording session, and a switch closes it before the next
        song is open, so the take is always the open song's. Called in the recorder's thread,
        it takes no gate: a switch may be waiting on that thread.
        """
        app = self.active
        if app is not None:
            app.take_logged(path)

    def open(self, name: str) -> SongApp:
        """Make `name` the open song, closing the one before.

        Raises:
            model.SongError: The song is missing, or open in another server.
            ApiError: 409 while a take records, the engine opens, or the old session will not
                close; 409 busy when the requests of the old song did not finish in time.
        """
        model.load(name, self.root)
        try:
            with self._gate.alone(self._switch_timeout):
                return self._switch(name)
        except TimeoutError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "busy") from None

    def _switch(self, name: str) -> SongApp:
        old = self._active
        if old is not None and old.name == name:
            return old
        if old is not None:
            if self.rec.taking():
                raise ApiError(HTTPStatus.CONFLICT, "a take is running", "take_running")
            if self.rec.stage() in ENGINE_BUSY:
                raise ApiError(
                    HTTPStatus.CONFLICT, "the recording engine is opening", "engine_busy"
                )
        new = self._make_app(name, self.rec, self.jobs, root=self.root)
        if old is not None:
            try:
                old.close()
            except rec.RecError as error:
                new.close()
                raise _rec_api_error(error) from None
        with self._lock:
            self._active = new
        return new

    # routes

    def list_songs(self) -> Reply:
        """Every song that loads, by name, and the open one."""
        songs = []
        for name in model.list_songs(self.root):
            try:
                songs.append(song_summary(model.load(name, self.root), self.root))
            except (model.SongError, OSError):
                continue
        active = self.active
        return reply_json(
            HTTPStatus.OK, {"songs": songs, "active": None if active is None else active.name}
        )

    def sources(self) -> Reply:
        """What a new song can be made of: beat files, own seeds, texts, genres, the pack.

        The genres are those a new beat may take; `pack_kit` says whether the owner's scanned
        pack can make a new beat's kit.
        """
        from redline.beat import handoff, pack
        from redline.beat.recipe import GENRES

        pack_kit = handoff.pack_can_kit(pack.CATALOG)
        return reply_json(
            HTTPStatus.OK,
            {
                "beats": audio_in(BEATS, self.root),
                "seeds": score_seeds(self.root),
                "texts": written_texts(self.root),
                "genres": sorted(GENRES),
                "pack_kit": pack_kit,
            },
        )

    def _new_beat(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or len(value) != 1 or set(value) - {"own", "file", "new"}:
            raise _unprocessable('beat is {"own": seed}, {"file": path} or {"new": {...}}')
        if "new" in value:
            return {"own": self._starter(value["new"])}
        if "own" in value:
            seed = _seed(value["own"], "beat.own")
            if seed not in score_seeds(self.root):
                raise _unprocessable(f"beat seed {seed} has no saved score")
            return {"own": seed}
        stored = value["file"]
        try:
            if not isinstance(stored, str) or Path(stored).parts[:1] != ("input",):
                raise model.SongError(f"beat.file: {stored!r} is not under input/")
            path = model.resolve_inside(stored, self.root, "beat.file")
        except model.SongError as error:
            raise _unprocessable(str(error)) from None
        if not path.is_file() or path.suffix.lower() not in AUDIO:
            raise _unprocessable(f"beat.file: {stored} is not an audio file")
        return {"file": stored}

    def _starter(self, value: Any) -> int:
        """A new own beat from a starter score: `{"genre": name|null, "kit": "auto"|...}`."""
        from redline.beat.handoff import BeatError, new_starter
        from redline.beat.recipe import RecipeError

        if not isinstance(value, dict) or set(value) - {"genre", "kit"}:
            raise _unprocessable('beat.new is {"genre": name or null, "kit": "auto"}')
        genre, kit = value.get("genre"), value.get("kit", "auto")
        if genre is not None and not isinstance(genre, str):
            raise _unprocessable("beat.new.genre is a genre name or null")
        if not isinstance(kit, str):
            raise _unprocessable("beat.new.kit is auto, synth or pack")
        try:
            return new_starter(self.root / "private" / "beats", genre, kit)
        except (BeatError, RecipeError) as error:
            raise _unprocessable(str(error)) from None

    def create(self, body: dict) -> Reply:
        """A new song as `redline song new` makes it, with every reference in input/refs."""
        only(body, "name", "beat", "text_seed")
        name = body.get("name")
        text_seed = body.get("text_seed")
        try:
            name = model.check_name(name)
        except model.SongError as error:
            raise _unprocessable(str(error)) from None
        beat = self._new_beat(body.get("beat"))
        if text_seed is not None:
            text_seed = _seed(text_seed, "text_seed")
        refs = tuple(audio_in(REFS, self.root))
        try:
            saved = model.create(name, beat, None, refs, text_seed, self.root)
        except model.SongExists as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "exists") from None
        except model.SongError as error:
            raise _unprocessable(str(error)) from None
        return reply_json(HTTPStatus.CREATED, {"name": saved.name})

    def open_song(self, body: dict) -> Reply:
        """Make the named song the open one."""
        only(body, "name")
        name = body.get("name")
        try:
            name = model.check_name(name)
        except model.SongError as error:
            raise _unprocessable(str(error)) from None
        if not (model.song_dir(name, self.root) / "song.json").is_file():
            raise ApiError(HTTPStatus.NOT_FOUND, f"{name}: no such song", "not_found")
        try:
            app = self.open(name)
        except model.SongError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "unopenable") from None
        return reply_json(HTTPStatus.OK, {"active": app.name})

    # dispatch

    def _no_song(self) -> Reply:
        return reply_json(HTTPStatus.CONFLICT, {"error": "no song is open", "code": "no_song"})

    def get(self, path: str, query: dict[str, list[str]]) -> Reply:
        """Answer a GET under /api/: the songs here, the rest by the open song."""
        try:
            if path == "/api/songs":
                return self.list_songs()
            if path == "/api/songs/sources":
                return self.sources()
        except ApiError as error:
            return reply_json(error.status, error.payload)
        with self._gate.shared():
            app = self.active
            return self._no_song() if app is None else app.get(path, query)

    def post(self, path: str, body: dict) -> Reply:
        """Answer a POST: the songs here, the rest by the open song."""
        try:
            if path == "/api/songs":
                return self.create(body)
            if path == "/api/songs/open":
                return self.open_song(body)
        except ApiError as error:
            return reply_json(error.status, error.payload)
        with self._gate.shared():
            app = self.active
            return self._no_song() if app is None else app.post(path, body)
