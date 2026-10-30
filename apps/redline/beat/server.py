"""The beat editor's server: a page on 127.0.0.1 that edits, checks, saves and renders a seed.

The guards (token, this machine's Host, JSON bodies of bounded size) are `redline.web.local`'s.
Samples reach the page only by a library name and a file's index in the manifest, never by a
path, and only once the file on disk matches the manifest's hash. Local-only libraries list no
files in the manifest, so they have nothing to serve: the exact render refuses them too, and the
page must not play what the render cannot.
"""

import contextlib
import io
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import numpy as np
import redline_dsp
import soundfile as sf

from redline.beat import library
from redline.beat.checks import Caution, check_score
from redline.beat.handoff import write_new_score
from redline.beat.marks import MarkError, StaleMark, add_mark, seed_marks
from redline.beat.recipe import Recipe, recipe_from_json
from redline.beat.render import (
    RATE,
    Rendered,
    RenderError,
    latest_score,
    notes_used,
    render_seed,
)
from redline.studio.server import (
    PAGE_KINDS,
    POLICY,
    SITE,
    Reply,
    reply_bytes,
    reply_json,
    send_reply,
)
from redline.web.local import BadRequest, LocalHandler, LocalServer, only

PAGE = "beat.html"
NOT_BUILT = "the beat page is not built: run `npm run build` in apps/studio"
SAVED = re.compile(r"score-(\d+)\.json")
FILE_ID = re.compile(r"\d{1,5}")
STEM = re.compile(r"[A-Za-z0-9_-]{1,64}")
RENDER_FILES = ("beat.wav", "render.json")
MAX_NOTE = 2000
# a score with per-step lanes on every row of 64 patterns runs to a few megabytes
BEAT_MAX_BODY = 4 * 1024 * 1024

Render = Callable[..., Rendered]


def beat_files(root: Path) -> dict[str, tuple[Path, str]]:
    """The built beat page at `/` and the build's assets; empty while beat.html is missing.

    Other HTML pages of the build are left out: a page is reached at `/` only, behind the token.
    """
    if not (root / PAGE).is_file():
        return {}
    files = {
        "/" + path.relative_to(root).as_posix(): (path, PAGE_KINDS[path.suffix])
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in PAGE_KINDS and path.suffix != ".html"
    }
    files["/"] = (root / PAGE, PAGE_KINDS[".html"])
    return files


def _caution(w: Caution) -> dict[str, str]:
    return {
        "kind": w.kind,
        "where": w.where,
        "message": w.message,
        "source": w.source,
        "text": str(w),
    }


class Refused(BadRequest):
    """A score the checks refuse, with every error."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        """Keep the errors for the answer."""
        super().__init__(HTTPStatus.UNPROCESSABLE_ENTITY, "the score has errors; nothing was saved")
        self.errors = errors


@dataclass
class RenderState:
    """The newest exact render: running, done or failed."""

    state: str = "idle"
    score_file: str | None = None
    error: str | None = None
    done: Rendered | None = None


class BeatStudio:
    """One seed's folders and what the page asks of them."""

    def __init__(
        self,
        seed: int,
        root: Path,
        work: Path,
        manifest: Path | None = None,
        render: Render = render_seed,
        rate: int = RATE,
    ) -> None:
        """Work on `<root>/<seed>/`, render into `<work>/beats/<seed>/`.

        Args:
            seed: The beat's seed.
            root: The beats folder holding the seed's folder and marks.jsonl.
            work: The work folder; samples are under `<work>/sounds/`.
            manifest: The sound manifest (default: the committed one).
            render: The exact render, `render_seed` unless a test swaps it.
            rate: The rate of the event table and the render.
        """
        self.seed = seed
        self.root = root
        self.work = work
        self.manifest = manifest
        self.rate = rate
        self.folder = root / str(seed)
        self.out = work / "beats" / str(seed)
        self._render = render
        self._save_lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = RenderState()

    def newest(self) -> Path:
        """The seed's newest saved score.

        Raises:
            BadRequest: The seed has none (404).
        """
        try:
            return latest_score(self.folder)
        except RenderError as error:
            raise BadRequest(HTTPStatus.NOT_FOUND, str(error)) from error

    def recipe(self) -> tuple[Recipe, Any]:
        """The seed's recipe and its JSON as written."""
        path = self.folder / "recipe.json"
        try:
            text = path.read_text(encoding="utf-8")
            return recipe_from_json(text), json.loads(text)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise BadRequest(HTTPStatus.NOT_FOUND, f"{path}: no readable recipe") from error

    def _sources(self) -> dict[str, library.Source]:
        try:
            return library.load(self.manifest)
        except library.LibraryError as error:
            raise BadRequest(HTTPStatus.INTERNAL_SERVER_ERROR, str(error)) from error

    def session(self) -> dict[str, Any]:
        """Everything the page opens with."""
        path = self.newest()
        text = path.read_text(encoding="utf-8")
        try:
            score = json.loads(text)
        except ValueError as error:
            raise BadRequest(HTTPStatus.CONFLICT, f"{path.name} is not JSON: {error}") from error
        _, recipe = self.recipe()
        try:
            events: Any = json.loads(redline_dsp.beat_events(text, float(self.rate)))
            events_error = None
        except ValueError as error:
            # a score edited by hand into one Rust refuses still opens, to be fixed on the page
            events, events_error = None, str(error)
        return {
            "seed": self.seed,
            "score_file": path.name,
            "score": score,
            "recipe": recipe,
            "instruments": json.loads(redline_dsp.beat_instruments()),
            "effects": [e for e in json.loads(redline_dsp.rack_effects()) if not e["offline_only"]],
            "rate": self.rate,
            "events": events,
            "events_error": events_error,
            "marks": seed_marks(self.root, self.seed),
            "libraries": self._libraries(text, score) if events is not None else [],
        }

    def _libraries(self, text: str, score: dict[str, Any]) -> list[dict[str, Any]]:
        """The libraries the score's samplers play, and the zones the notes they play need."""
        used = notes_used(text, float(self.rate), score)
        by_library: dict[str, list[str]] = {}
        for name in sorted(used):
            by_library.setdefault(score["sounds"][name]["library"], []).append(name)
        sources = self._sources()
        out = []
        for name, sounds in sorted(by_library.items()):
            entry = sources.get(name)
            item: dict[str, Any] = {
                "name": name,
                "sounds": sounds,
                "licence": entry.licence if entry else None,
                "commercial": entry.commercial if entry else False,
                "kind": entry.kind if entry else None,
                "fetched": False,
                "zones": [],
                "error": None if entry else f"{name} is not in the sound library",
            }
            out.append(item)
            if entry is None:
                continue
            notes = sorted(set().union(*(used[s] for s in sounds)))
            try:
                zones = library.zones(name, self.work / "sounds", notes, self.manifest)
                item["zones"] = [self._zone_info(name, entry, zone) for zone in zones]
                item["fetched"] = True
            except (library.LibraryError, OSError, sf.LibsndfileError) as error:
                item["error"] = str(error)
        return out

    def _zone_info(self, name: str, entry: library.Source, zone: library.Zone) -> dict[str, Any]:
        folder = self.work / "sounds" / name
        index = next(i for i, f in enumerate(entry.files) if folder / f.path == zone.path)
        info = sf.info(zone.path)
        return {
            "library": name,
            "file": str(index),
            "root": zone.note,
            "vel": list(zone.vel),
            "rate": info.samplerate,
            "channels": info.channels,
            "frames": info.frames,
        }

    def zone(self, name: str, file_id: str) -> tuple[bytes, int, int, int, int]:
        """A manifest file's samples as little-endian int16, checked before it is read.

        Returns:
            The PCM bytes, the rate, the channels, the frames and the root note.

        Raises:
            BadRequest: An unknown library or file (404), a local-only library or a path that
                resolves out of its folder (403), a missing file (404), bytes that do not match
                the manifest (409).
        """
        if not FILE_ID.fullmatch(file_id):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "a file is its index in the manifest")
        entry = self._sources().get(name)
        if entry is None:
            raise BadRequest(HTTPStatus.NOT_FOUND, f"no sound library named {name!r}")
        if entry.kind != "fetch":
            raise BadRequest(HTTPStatus.FORBIDDEN, f"{name} is local only: it is not served")
        index = int(file_id)
        if index >= len(entry.files):
            raise BadRequest(HTTPStatus.NOT_FOUND, f"{name} has no file {index}")
        item = entry.files[index]
        folder = self.work / "sounds" / name
        target = folder / item.path
        # the manifest's paths are checked on load; a symlink on disk is not
        if not target.resolve().is_relative_to(folder.resolve()):
            raise BadRequest(HTTPStatus.FORBIDDEN, f"{name} file {index} resolves outside {folder}")
        if not target.is_file():
            raise BadRequest(
                HTTPStatus.NOT_FOUND,
                f"{name} is not fetched: run `redline beat sounds fetch {name}`",
            )
        # checked and decoded from the same bytes, so a swap after the check is not served
        data = target.read_bytes()
        if len(data) != item.size or library.git_sha1(data) != item.git_sha1:
            raise BadRequest(
                HTTPStatus.CONFLICT, f"{name} file {index} does not match its hash: fetch it again"
            )
        try:
            samples, rate = sf.read(io.BytesIO(data), dtype="int16", always_2d=True)
        except sf.LibsndfileError as error:
            raise BadRequest(HTTPStatus.CONFLICT, f"{name} file {index}: {error}") from error
        if samples.shape[1] > 2:
            raise BadRequest(HTTPStatus.CONFLICT, f"{name} file {index}: more than 2 channels")
        pcm = np.ascontiguousarray(samples, dtype="<i2").tobytes()
        return pcm, int(rate), samples.shape[1], samples.shape[0], item.note

    def check(self, score: dict[str, Any]) -> tuple[tuple[str, ...], list[dict[str, str]]]:
        """The errors and warnings of a score against the seed's recipe."""
        recipe, _ = self.recipe()
        checked = check_score(json.dumps(score), recipe, self._sources().keys())
        return checked.errors, [_caution(w) for w in checked.warnings]

    def save(self, score: dict[str, Any], base: str) -> tuple[str, list[dict[str, str]]]:
        """Check a score and save it as the next `score-K.json`.

        Args:
            score: The edited score.
            base: The saved score the edit started from; it must still be the newest.

        Returns:
            The new file's name and the warnings it keeps.

        Raises:
            BadRequest: A base that is not a score name (400) or not the newest (409).
            Refused: The checks found errors (422); nothing is written.
        """
        found = SAVED.fullmatch(base)
        if found is None:
            raise BadRequest(HTTPStatus.BAD_REQUEST, "base is a saved score's name, score-K.json")
        with self._save_lock:
            newest = self.newest().name
            if base != newest:
                raise BadRequest(
                    HTTPStatus.CONFLICT, f"{newest} is newer than {base}: reload before saving"
                )
            errors, warnings = self.check(score)
            if errors:
                raise Refused(errors)
            text = json.dumps(score, ensure_ascii=False, indent=1) + "\n"
            name = write_new_score(self.folder, int(found.group(1)) + 1, text)
        return name, warnings

    def mark(self, bar: int, verdict: str, note: str, base: str) -> dict[str, Any]:
        """A good or bad mark on a bar of the score the page shows, through `redline.beat.marks`.

        Args:
            bar: The bar, 1-based.
            verdict: `good` or `bad`.
            note: Why, in the owner's words.
            base: The saved score the page shows; it must still be the newest.

        Raises:
            BadRequest: A base that is not a score name or a bad mark (400), or a base that is
                not the newest saved score (409).
        """
        if not SAVED.fullmatch(base):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "base is a saved score's name, score-K.json")
        # a save between the newest check and the read would leave the mark on an old score
        with self._save_lock:
            try:
                return add_mark(self.root, self.seed, bar, verdict, note, base=base)
            except StaleMark as error:
                raise BadRequest(HTTPStatus.CONFLICT, str(error)) from error
            except (MarkError, OSError) as error:
                raise BadRequest(HTTPStatus.BAD_REQUEST, str(error)) from error

    def start_render(self, score_file: str) -> None:
        """Start the exact render of a saved score in a worker thread.

        Raises:
            BadRequest: Not a saved score of the seed (400, 404), or a render runs (409).
        """
        if not SAVED.fullmatch(score_file):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "score_file is a saved score's name")
        path = self.folder / score_file
        if not path.is_file():
            raise BadRequest(HTTPStatus.NOT_FOUND, f"seed {self.seed} has no {score_file}")
        if not self._render_lock.acquire(blocking=False):
            raise BadRequest(HTTPStatus.CONFLICT, "a render is running")
        with self._state_lock:
            self._state = RenderState("running", score_file)
        threading.Thread(target=self._run_render, args=(path,), daemon=True).start()

    def _run_render(self, path: Path) -> None:
        state = RenderState("failed", path.name, error="the render stopped")
        try:
            done = self._render(self.seed, self.root, self.work, self.rate, path, self.manifest)
            state = RenderState("done", path.name, done=done)
        except Exception as error:  # noqa: BLE001  (any failure is the page's to show)
            state = RenderState("failed", path.name, error=str(error) or type(error).__name__)
        finally:
            with self._state_lock:
                self._state = state
            self._render_lock.release()

    def render_status(self) -> dict[str, Any]:
        """The newest render's state, for the page to poll."""
        with self._state_lock:
            s = self._state
        out: dict[str, Any] = {"state": s.state, "score_file": s.score_file, "error": s.error}
        if s.done is not None:
            out |= {
                "frames": s.done.frames,
                "seconds": s.done.frames / self.rate,
                "sounds": list(s.done.sounds),
                "non_commercial": list(s.done.non_commercial),
            }
        return out

    def render_file(self, name: str) -> tuple[bytes, str]:
        """A file of the newest render by its name in the fixed set.

        Raises:
            BadRequest: A name outside the set (400), a render running (409), a file that
                resolves out of the render folder (403) or is missing (404).
        """
        if name not in self._render_names():
            raise BadRequest(HTTPStatus.BAD_REQUEST, "not a file a render writes")
        if self._render_lock.locked():
            raise BadRequest(HTTPStatus.CONFLICT, "a render is running")
        target = self.out / name
        if not target.resolve().is_relative_to(self.out.resolve()):
            raise BadRequest(HTTPStatus.FORBIDDEN, f"{name} resolves outside {self.out}")
        if not target.is_file():
            raise BadRequest(HTTPStatus.NOT_FOUND, f"no {name}: render first")
        kind = "application/json" if name.endswith(".json") else "audio/wav"
        return target.read_bytes(), kind

    def _render_names(self) -> set[str]:
        """beat.wav, render.json and a stem per sound the newest render.json lists."""
        names = set(RENDER_FILES)
        with contextlib.suppress(OSError, ValueError, TypeError, AttributeError):
            report = json.loads((self.out / "render.json").read_text(encoding="utf-8"))
            names |= {
                f"stems/{s}.wav"
                for s in report["sounds"]
                if isinstance(s, str) and STEM.fullmatch(s)
            }
        return names


def _score(body: dict) -> dict[str, Any]:
    score = body.get("score")
    if not isinstance(score, dict):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "score is a JSON object")
    return score


def _one(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key, [])
    if len(values) != 1:
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"give one {key}")
    return values[0]


class Handler(LocalHandler):
    """Answers the beat page; the seed and the token sit on the server."""

    server: "BeatServer"  # pyright: ignore[reportIncompatibleVariableOverride]
    max_body = BEAT_MAX_BODY

    def send(self, status: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        """Answer with the guard headers and the studio's page policy on."""
        super().send(status, body, kind, {"Content-Security-Policy": POLICY, **(extra or {})})

    def do_GET(self) -> None:
        """The page once it is built, a note on how to build it until then, and the API."""
        url = urlsplit(self.path)
        given = parse_qs(url.query).get("t", [None])[0]
        if (
            url.path == "/"
            and "/" not in self.server.files
            and self._host_ok()
            and self._token_ok(given)
        ):
            # built after the server started: picked up on reload
            self.server.files = beat_files(self.server.site)
            if "/" not in self.server.files:
                return self.error(HTTPStatus.NOT_FOUND, NOT_BUILT)
        return super().do_GET()

    def api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """The session, a zone's samples, the render's state and files."""
        beat = self.server.beat
        self._answered(lambda: beat_get(beat, path, query))

    def api_post(self, path: str, body: dict) -> None:
        """Check, save, render and mark."""
        beat = self.server.beat
        self._answered(lambda: beat_post(beat, path, body))

    def _answered(self, call: Callable[[], Reply]) -> None:
        try:
            send_reply(self, call())
        except BadRequest:
            raise
        except Exception as error:  # noqa: BLE001
            # the page shows a JSON error; a dropped connection tells it nothing
            self.error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(error).__name__}: {error}")


def beat_get(beat: BeatStudio, path: str, query: dict[str, list[str]]) -> Reply:
    """The beat editor's read-only calls, for the BeatStudio given.

    Raises:
        BadRequest: The request is refused; the status is the error's.
    """
    if path == "/api/beat/session":
        return reply_json(HTTPStatus.OK, beat.session())
    if path == "/api/beat/zone":
        pcm, rate, channels, frames, root = beat.zone(_one(query, "library"), _one(query, "file"))
        headers = {
            "X-Rate": str(rate),
            "X-Channels": str(channels),
            "X-Frames": str(frames),
            "X-Root": str(root),
        }
        return reply_bytes(HTTPStatus.OK, pcm, "application/octet-stream", headers)
    if path == "/api/beat/render/status":
        return reply_json(HTTPStatus.OK, beat.render_status())
    if path == "/api/beat/render":
        data, kind = beat.render_file(_one(query, "file"))
        return reply_bytes(HTTPStatus.OK, data, kind)
    return reply_json(HTTPStatus.NOT_FOUND, {"error": "no such call"})


def beat_post(beat: BeatStudio, path: str, body: dict) -> Reply:
    """The beat editor's calls that change something, for the BeatStudio given.

    Raises:
        BadRequest: The request is refused; the status is the error's.
    """
    if path == "/api/beat/check":
        only(body, "score")
        errors, warnings = beat.check(_score(body))
        return reply_json(HTTPStatus.OK, {"errors": list(errors), "warnings": warnings})
    if path == "/api/beat/save":
        only(body, "score", "base")
        base = body.get("base")
        if not isinstance(base, str):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "base is a saved score's name")
        try:
            name, warnings = beat.save(_score(body), base)
        except Refused as refused:
            return reply_json(
                refused.status, {"error": str(refused), "errors": list(refused.errors)}
            )
        return reply_json(HTTPStatus.OK, {"file": name, "warnings": warnings})
    if path == "/api/beat/render":
        only(body, "score_file")
        score_file = body.get("score_file")
        if not isinstance(score_file, str):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "score_file is a saved score's name")
        beat.start_render(score_file)
        return reply_json(HTTPStatus.ACCEPTED, beat.render_status())
    if path == "/api/beat/mark":
        return _mark(beat, body)
    return reply_json(HTTPStatus.NOT_FOUND, {"error": "no such call"})


def _mark(beat: BeatStudio, body: dict) -> Reply:
    only(body, "bar", "verdict", "note", "base")
    bar, verdict, note = body.get("bar"), body.get("verdict"), body.get("note", "")
    base = body.get("base")
    if not isinstance(base, str):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "base is a saved score's name")
    if not isinstance(bar, int) or isinstance(bar, bool):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "bar is a whole number from 1")
    if not isinstance(verdict, str) or not isinstance(note, str):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "verdict and note are text")
    if len(note) > MAX_NOTE:
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"a note is at most {MAX_NOTE} characters")
    return reply_json(HTTPStatus.OK, beat.mark(bar, verdict, note, base))


class BeatServer(LocalServer):
    """The HTTP server with the seed and its token."""

    def __init__(self, address: tuple[str, int], beat: BeatStudio, token: str, site: Path) -> None:
        """Bind and keep the seed and where its page is built."""
        super().__init__(address, Handler, token, beat_files(site))
        self.beat = beat
        self.site = site


def make_server(
    beat: BeatStudio, token: str, port: int = 0, site: Path | None = None
) -> BeatServer:
    """A server on 127.0.0.1 only; port 0 takes a free one, site defaults to the studio's build."""
    return BeatServer(("127.0.0.1", port), beat, token, SITE if site is None else site)
