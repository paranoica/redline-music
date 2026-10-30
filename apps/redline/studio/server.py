"""The studio: a page on 127.0.0.1 that plays the track and re-renders it as settings move.

One render runs at a time and the newest request wins: an older one stops between stages.
The page itself runs the chain live in the browser (see `redline.engine.live`); the server
hands it the stems, the numbers only the whole track gives, and the exact render. The guards
of the page's server live in `redline.web.local`.
"""

import contextlib
import io
import json
import math
import os
import threading
import time
from collections.abc import Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core import native
from redline.core.loudness import integrated_lufs
from redline.engine import live, racks_render
from redline.engine.render import Cancelled, Engine, Rendered
from redline.engine.settings import PRESET_NAME, VERSION, Settings, from_dict, schema, to_dict
from redline.web.local import KINDS, BadRequest, LocalHandler, LocalServer, only

Samples = npt.NDArray[np.floating]

# the page's own build writes it next to the package
SITE = Path(__file__).resolve().parents[2] / "studio" / "dist"
NOT_BUILT = "the studio page is not built: run `npm --prefix apps/studio run build`"
PAGE_KINDS = {**KINDS, ".wasm": "application/wasm"}
STEMS = ("vocal", "beat", "original")
# 'wasm-unsafe-eval' lets the page compile its WebAssembly without allowing eval of
# JavaScript; worker-src keeps workers to the page's own files (an AudioWorklet's module
# falls under script-src, so 'self' covers it); fonts stay the only outside source.
POLICY = (
    "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; "
    "media-src 'self' blob:; img-src 'self' data:; "
    "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com"
)


def page_files(root: Path) -> dict[str, tuple[Path, str]]:
    """Every file of the built page by its URL path; empty while index.html is missing."""
    if not (root / "index.html").is_file():
        return {}
    files = {
        "/" + path.relative_to(root).as_posix(): (path, PAGE_KINDS[path.suffix])
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in PAGE_KINDS
    }
    files["/"] = files["/index.html"]
    return files


def wav_bytes(samples: Samples, rate: int) -> bytes:
    """16-bit WAV in memory: enough to listen to, half the bytes of float."""
    buffer = io.BytesIO()
    sf.write(buffer, np.clip(samples, -1.0, 1.0), rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def float_wav_bytes(samples: Samples, rate: int) -> bytes:
    """32-bit float WAV in memory: the samples as they are, past full scale too."""
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="FLOAT")
    return buffer.getvalue()


class Studio:
    """The track, its engine and what the page has asked for."""

    def __init__(
        self,
        engine: Engine,
        original: Samples,
        folder: Path,
        track_key: str,
        references: Sequence[tuple[str, Samples, int]] = (),
        classic: bool = False,
    ) -> None:
        """Hold the engine of one track.

        Args:
            engine: The track's engine.
            original: The track as Suno made it, for A/B.
            folder: Where settings and presets are saved.
            track_key: The track's folder name under folder.
            references: Name, samples and rate of each reference, played in the A/B.
            classic: Render exactly with the Python engine instead of the racks the page
                plays live.
        """
        self.references = [
            (name, samples, rate, integrated_lufs(samples, rate))
            for name, samples, rate in references
        ]
        self.engine = engine
        self.classic = classic
        self.original = original
        self.folder = folder
        self.track_key = track_key
        self.rate = engine.context.rate
        self.latest_seq = 0
        self.original_lufs = integrated_lufs(original, self.rate)
        # every settings value a whole render finished with: any of them may be saved
        self.exact: set[Settings] = set()
        self._seq_lock = threading.Lock()
        # a whole render takes a minute; a preview must not queue behind it
        self._render_lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._analysis_lock = threading.Lock()
        self._analysis_asked = 0

    def _claim(self, seq: int) -> bool:
        with self._seq_lock:
            if seq < self.latest_seq:
                return False
            self.latest_seq = seq
            return True

    def _run(self, seq: int, work: Any, lock: "threading.Lock | None" = None) -> Any:
        if not self._claim(seq):
            return None
        with lock or self._render_lock:
            if seq != self.latest_seq:
                return None
            try:
                return work(lambda: seq != self.latest_seq)
            except Cancelled:
                return None

    def render(self, settings: Settings, seq: int) -> Rendered | None:
        """The whole track at these settings; None when a newer request came in."""

        def work(cancelled: Any) -> Rendered:
            if self.classic:
                return self.engine.render(settings, cancelled)
            try:
                return racks_render.render(self.engine, settings, cancelled)
            except ValueError as error:
                raise BadRequest(HTTPStatus.BAD_REQUEST, str(error)) from error

        rendered = self._run(seq, work)
        if rendered is not None:
            self.exact.add(settings)
        return rendered

    def preview(self, settings: Settings, seq: int, start: int, end: int) -> Samples | None:
        """The loop at these settings, rendered on the loop alone; None when superseded."""
        return self._run(
            seq, lambda _: self.engine.preview(settings, start, end), self._preview_lock
        )

    def current(self) -> Settings:
        """The settings saved for the track, or the defaults when none are or they do not load."""
        saved = self.folder / self.track_key / "settings.json"
        if saved.exists():
            with contextlib.suppress(ValueError):
                data = json.loads(saved.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return from_dict(data)
        return Settings()

    def stem(self, name: str) -> Samples:
        """The vocal or the beat as the engine holds them, or the two summed as the original."""
        engine = self.engine
        if name == "vocal":
            return engine.vocal
        if name == "beat":
            return engine.beat
        return engine.vocal + engine.beat

    def analysis(self, settings: Settings, newest_only: bool = True) -> dict[str, Any]:
        """What `live.analysis` measures for these settings.

        Args:
            settings: The settings to measure.
            newest_only: Give way to an analysis asked for later, as the page's edits do.
                The session's analysis does not: no later one replaces it, so it neither
                cancels nor is cancelled.

        Raises:
            BadRequest: The engine cannot measure these settings, or a newer analysis was
                asked for meanwhile (409).
        """
        mine = 0
        if newest_only:
            # the page drops a reply it no longer waits for, so only the newest is worth the work
            with self._seq_lock:
                self._analysis_asked += 1
                mine = self._analysis_asked

        def newer() -> bool:
            return newest_only and mine != self._analysis_asked

        with self._analysis_lock:
            try:
                if newer():
                    raise Cancelled
                return live.analysis(self.engine, settings, newer)
            except Cancelled as error:
                raise BadRequest(HTTPStatus.CONFLICT, "a newer analysis was asked for") from error
            except ValueError as error:
                raise BadRequest(HTTPStatus.BAD_REQUEST, str(error)) from error

    def save(self, settings: Settings) -> Path:
        """Write the settings the last exact render was made with."""
        if settings not in self.exact:
            raise BadRequest(HTTPStatus.CONFLICT, "render these settings exactly before saving")
        path = self.folder / self.track_key / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace(path, json.dumps(to_dict(settings), indent=1))
        return path

    def presets(self) -> dict[str, dict]:
        """Saved presets by name: only the settings that differ from the defaults."""
        folder = self.folder / "presets"
        out = {}
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            # a preset edited by hand into something else is skipped, not fatal
            with contextlib.suppress(ValueError):
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    out[path.stem] = data
        return out

    def save_preset(self, name: str, settings: Settings) -> None:
        """Keep a named preset of what differs from the defaults."""
        if not PRESET_NAME.fullmatch(name) or name.strip() != name or name in (".", ".."):
            raise BadRequest(
                HTTPStatus.BAD_REQUEST, "a preset name is 1-40 letters, digits, spaces"
            )
        defaults = to_dict(Settings())
        changed = {k: v for k, v in to_dict(settings).items() if defaults[k] != v}
        if settings.rack:
            # a rack loads only from a file that says its version
            changed = {"version": VERSION, **changed}
        folder = self.folder / "presets"
        folder.mkdir(parents=True, exist_ok=True)
        _replace(folder / f"{name}.json", json.dumps(changed, ensure_ascii=False))


def _replace(path: Path, text: str) -> None:
    # two saves at once, or a crash mid-write, must not leave half a file behind
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _settings(body: dict) -> Settings:
    data = body.get("settings")
    if not isinstance(data, dict):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "settings are an object")
    try:
        return from_dict(data)
    except ValueError as error:
        raise BadRequest(HTTPStatus.BAD_REQUEST, str(error)) from error


def _seq(body: dict) -> int:
    seq = body.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise BadRequest(HTTPStatus.BAD_REQUEST, "seq is a whole number from 0")
    return seq


Reply = tuple[int, bytes, dict[str, str]]


def reply_bytes(status: int, body: bytes, kind: str, extra: dict[str, str] | None = None) -> Reply:
    """An answer as data: the status, the body and the headers, Content-Type among them."""
    return status, body, {"Content-Type": kind, **(extra or {})}


def reply_json(status: int, data: Any) -> Reply:
    """A JSON answer as data."""
    return reply_bytes(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")


def send_reply(handler: LocalHandler, reply: Reply) -> None:
    """Write an answer made by a route function."""
    status, body, headers = reply
    kind = headers.get("Content-Type", "application/octet-stream")
    handler.send(status, body, kind, {k: v for k, v in headers.items() if k != "Content-Type"})


class Handler(LocalHandler):
    """Answers the page; the studio and the token sit on the server."""

    server: "StudioServer"  # pyright: ignore[reportIncompatibleVariableOverride]

    def send(self, status: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        """Answer with the guard headers and the studio's own policy on."""
        super().send(status, body, kind, {"Content-Security-Policy": POLICY, **(extra or {})})

    def do_GET(self) -> None:
        """The page once it is built, a note on how to build it until then, and the API."""
        url = urlsplit(self.path)
        given = parse_qs(url.query).get("t", [None])[0]
        if (
            url.path in ("/", "/index.html")
            and "/" not in self.server.files
            and self._host_ok()
            and self._token_ok(given)
        ):
            # built after the server started: picked up on reload
            self.server.files = page_files(self.server.site)
            if "/" not in self.server.files:
                body = NOT_BUILT.encode()
                return self.send(HTTPStatus.SERVICE_UNAVAILABLE, body, "text/plain; charset=utf-8")
        return super().do_GET()

    def api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """The session, the stems, the references, and the old page's calls."""
        send_reply(self, studio_get(self.server.studio, path, query))

    def api_post(self, path: str, body: dict) -> None:
        """Analyses, renders, saves and presets."""
        send_reply(self, studio_post(self.server.studio, path, body))


def studio_get(studio: Studio, path: str, query: dict[str, list[str]]) -> Reply:
    """The studio's read-only calls, for the studio given.

    Raises:
        BadRequest: The request is refused; the status is the error's.
    """
    if path == "/api/session":
        return reply_json(HTTPStatus.OK, _session(studio))
    if path == "/api/stem":
        return _stem(studio, query.get("name", []))
    if path == "/api/schema":
        return reply_json(HTTPStatus.OK, _schema(studio))
    if path == "/api/presets":
        return reply_json(HTTPStatus.OK, studio.presets())
    if path == "/api/effects":
        # empty without the Rust module: the page then says the rack is not available
        return reply_json(HTTPStatus.OK, native.rack_effects() or [])
    if path == "/api/references":
        return reply_json(HTTPStatus.OK, _references(studio))
    if path == "/api/reference":
        return _reference(studio, query.get("i", []))
    if path == "/api/reference_span":
        return _reference_span(studio, query)
    if path == "/api/original":
        return reply_bytes(
            HTTPStatus.OK,
            wav_bytes(studio.original, studio.rate),
            "audio/wav",
            {"X-Lufs": f"{studio.original_lufs:.2f}"},
        )
    return reply_json(HTTPStatus.NOT_FOUND, {"error": "no such call"})


def _error(status: int, message: str) -> Reply:
    return reply_json(status, {"error": message})


def _session(studio: Studio) -> dict[str, Any]:
    settings = studio.current()
    numbers = studio.analysis(settings, newest_only=False)
    frames = len(studio.engine.vocal)
    return {
        "track": {
            "name": studio.track_key,
            "rate": studio.rate,
            "frames": frames,
            "seconds": frames / studio.rate,
        },
        "settings": to_dict(settings),
        # empty without the Rust module: the page then says the rack is not available
        "schema": {**schema(), "effects": native.rack_effects() or []},
        "analysis": numbers,
        "stale": {name: sorted(keys) for name, keys in live.STALE_AFTER.items()},
        "presets": studio.presets(),
        "references": _references(studio),
        **_racks(settings, numbers),
    }


def _racks(settings: Settings, numbers: dict[str, Any]) -> dict[str, Any]:
    try:
        return {"racks": live.racks(settings, numbers)}
    except ValueError as error:
        # saved settings the engine renders but the live racks cannot hold still open
        return {"racks": None, "racks_error": str(error)}


def _references(studio: Studio) -> list[dict[str, Any]]:
    return [
        {"index": i, "name": name, "seconds": len(x) / rate, "lufs": lufs}
        for i, (name, x, rate, lufs) in enumerate(studio.references)
    ]


def _stem(studio: Studio, asked: list[str]) -> Reply:
    if len(asked) != 1 or asked[0] not in STEMS:
        return _error(HTTPStatus.BAD_REQUEST, f"a stem is one of {', '.join(STEMS)}")
    samples = studio.stem(asked[0])
    return reply_bytes(
        HTTPStatus.OK,
        float_wav_bytes(samples, studio.rate),
        "audio/wav",
        {"X-Lufs": f"{integrated_lufs(samples, studio.rate):.2f}"},
    )


def _which(studio: Studio, asked: list[str]) -> int | None:
    given = asked[0] if len(asked) == 1 else ""
    if not (given.isascii() and given.isdigit() and len(given) < 4):
        return None
    return int(given) if int(given) < len(studio.references) else None


def _reference_span(studio: Studio, query: dict[str, list[str]]) -> Reply:
    which = _which(studio, query.get("i", []))
    try:
        start, end = float(query["start"][0]), float(query["end"][0])
    except (KeyError, ValueError):
        return _error(HTTPStatus.BAD_REQUEST, "start and end are seconds")
    if which is None:
        return _error(HTTPStatus.BAD_REQUEST, "no such reference")
    _, samples, rate, _ = studio.references[which]
    if not (
        math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= len(samples) / rate
    ):
        return _error(HTTPStatus.BAD_REQUEST, "the span lies outside the reference")
    lufs = integrated_lufs(samples[int(start * rate) : int(end * rate)], rate)
    return reply_json(HTTPStatus.OK, {"lufs": lufs if math.isfinite(lufs) else None})


def _reference(studio: Studio, asked: list[str]) -> Reply:
    which = _which(studio, asked)
    if which is None:
        return _error(HTTPStatus.BAD_REQUEST, "no such reference")
    _, samples, rate, lufs = studio.references[which]
    return reply_bytes(
        HTTPStatus.OK, wav_bytes(samples, rate), "audio/wav", {"X-Lufs": f"{lufs:.2f}"}
    )


def _schema(studio: Studio) -> dict:
    saved = studio.folder / studio.track_key / "settings.json"
    current = studio.current()
    ctx = studio.engine.context
    return {
        **schema(),
        "current": to_dict(current),
        "track": {
            "seconds": len(studio.original) / studio.rate,
            "rate": studio.rate,
            "tuning_cents": ctx.tuning_cents,
            "master_lufs": ctx.master_lufs,
            "original_lufs": studio.original_lufs,
            "saved": str(saved),
        },
    }


def studio_post(studio: Studio, path: str, body: dict) -> Reply:
    """The studio's analyses, renders, saves and presets, for the studio given.

    Raises:
        BadRequest: The request is refused; the status is the error's.
    """
    if path == "/api/analysis":
        return _analysis(studio, body)
    if path == "/api/preview":
        return _preview(studio, body)
    if path == "/api/render":
        return _render(studio, body)
    if path == "/api/save":
        only(body, "settings")
        saved = studio.save(_settings(body))
        return reply_json(HTTPStatus.OK, {"path": str(saved)})
    if path == "/api/preset":
        only(body, "name", "settings")
        name = body.get("name")
        if not isinstance(name, str):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "a preset needs a name")
        studio.save_preset(name, _settings(body))
        return reply_json(HTTPStatus.OK, {"name": name})
    return _error(HTTPStatus.NOT_FOUND, "no such call")


def _analysis(studio: Studio, body: dict) -> Reply:
    only(body, "settings")
    settings = _settings(body)
    numbers = studio.analysis(settings)
    try:
        racks = live.racks(settings, numbers)
    except ValueError as error:
        raise BadRequest(HTTPStatus.BAD_REQUEST, str(error)) from error
    lanes = live.vocal_lanes(settings, numbers)
    return reply_json(HTTPStatus.OK, {"analysis": numbers, "racks": racks, "lanes": lanes})


def _preview(studio: Studio, body: dict) -> Reply:
    only(body, "settings", "seq", "start", "end")
    settings, seq = _settings(body), _seq(body)
    start, end = body.get("start"), body.get("end")
    total = len(studio.original) / studio.rate
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (start, end)):
        raise BadRequest(HTTPStatus.BAD_REQUEST, "start and end are seconds")
    if not 0 <= start < end <= total:  # type: ignore[operator]
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"the loop lies within 0-{total:.2f} s")
    first, last = int(start * studio.rate), int(end * studio.rate)  # type: ignore[operator]
    began = time.monotonic()
    samples = studio.preview(settings, seq, first, last)
    if samples is None:
        return _error(HTTPStatus.CONFLICT, "a newer request came in")
    took = (time.monotonic() - began) * 1000
    print(f"preview {end - start:.1f} s in {took:.0f} ms")  # type: ignore[operator]
    lufs = integrated_lufs(studio.original[first:last], studio.rate)
    return reply_bytes(
        HTTPStatus.OK,
        wav_bytes(samples, studio.rate),
        "audio/wav",
        {
            "X-Render-Ms": f"{took:.0f}",
            "X-Lufs": f"{integrated_lufs(samples, studio.rate):.2f}",
            "X-Original-Lufs": f"{lufs:.2f}",
        },
    )


def _render(studio: Studio, body: dict) -> Reply:
    only(body, "settings", "seq")
    settings, seq = _settings(body), _seq(body)
    began = time.monotonic()
    rendered = studio.render(settings, seq)
    if rendered is None:
        return _error(HTTPStatus.CONFLICT, "a newer request came in")
    took = (time.monotonic() - began) * 1000
    print(f"exact render in {took:.0f} ms")
    return reply_bytes(
        HTTPStatus.OK,
        wav_bytes(rendered.final, studio.rate),
        "audio/wav",
        {
            "X-Render-Ms": f"{took:.0f}",
            "X-Lufs": f"{integrated_lufs(rendered.final, studio.rate):.2f}",
        },
    )


class StudioServer(LocalServer):
    """The HTTP server with the studio and its token."""

    def __init__(self, address: tuple[str, int], studio: Studio, token: str, site: Path) -> None:
        """Bind and keep the studio and where its page is built."""
        super().__init__(address, Handler, token, page_files(site))
        self.studio = studio
        self.site = site


def make_server(
    studio: Studio, token: str, port: int = 0, site: Path | None = None
) -> StudioServer:
    """A server on 127.0.0.1 only; port 0 takes a free one, site defaults to SITE."""
    return StudioServer(("127.0.0.1", port), studio, token, SITE if site is None else site)
