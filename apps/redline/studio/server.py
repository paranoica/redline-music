"""The studio: a page on 127.0.0.1 that plays the track and re-renders it as settings move.

One render runs at a time and the newest request wins: an older one stops between stages.
Every API call carries the token printed with the page address, and the Host must be this
machine at this port, so neither another site in the browser nor a rebound name gets in.
"""

import contextlib
import hmac
import io
import json
import re
import threading
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core.loudness import integrated_lufs
from redline.engine.render import Cancelled, Engine, Rendered
from redline.engine.settings import Settings, from_dict, schema

Samples = npt.NDArray[np.floating]

# the page lives next to the package, in apps/studio-web
SITE = Path(__file__).resolve().parents[2] / "studio-web"
KINDS = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def site_files(root: Path = SITE) -> dict[str, tuple[Path, str]]:
    """Every page file by its URL path, listed once: nothing outside this map is served."""
    files = {
        "/" + path.relative_to(root).as_posix(): (path, KINDS[path.suffix])
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in KINDS
    }
    files["/"] = files["/index.html"]
    return files


MAX_BODY = 64 * 1024
PRESET_NAME = re.compile(r"[\w \-]{1,40}")
HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; media-src 'self' blob:; img-src 'self' data:; "
        "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com"
    ),
}


class BadRequest(Exception):
    """The request asks for something the studio does not take."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        """Keep the status to answer with."""
        super().__init__(message)
        self.status = status


def wav_bytes(samples: Samples, rate: int) -> bytes:
    """16-bit WAV in memory: enough to listen to, half the bytes of float."""
    buffer = io.BytesIO()
    sf.write(buffer, np.clip(samples, -1.0, 1.0), rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class Studio:
    """The track, its engine and what the page has asked for."""

    def __init__(self, engine: Engine, original: Samples, folder: Path, track_key: str) -> None:
        """Hold the engine of one track.

        Args:
            engine: The track's engine.
            original: The track as Suno made it, for A/B.
            folder: Where settings and presets are saved.
            track_key: The track's folder name under folder.
        """
        self.engine = engine
        self.original = original
        self.folder = folder
        self.track_key = track_key
        self.rate = engine.context.rate
        self.latest_seq = 0
        self.original_lufs = integrated_lufs(original, self.rate)
        self.exact: Settings | None = None
        self._seq_lock = threading.Lock()
        self._render_lock = threading.Lock()

    def _claim(self, seq: int) -> bool:
        with self._seq_lock:
            if seq < self.latest_seq:
                return False
            self.latest_seq = seq
            return True

    def _run(self, seq: int, work: Any) -> Any:
        if not self._claim(seq):
            return None
        with self._render_lock:
            if seq != self.latest_seq:
                return None
            try:
                return work(lambda: seq != self.latest_seq)
            except Cancelled:
                return None

    def render(self, settings: Settings, seq: int) -> Rendered | None:
        """The whole track at these settings; None when a newer request came in."""
        rendered = self._run(seq, lambda cancelled: self.engine.render(settings, cancelled))
        if rendered is not None:
            self.exact = settings
        return rendered

    def preview(self, settings: Settings, seq: int, start: int, end: int) -> Samples | None:
        """The loop at these settings, rendered on the loop alone; None when superseded."""
        return self._run(seq, lambda _: self.engine.preview(settings, start, end))

    def save(self, settings: Settings) -> Path:
        """Write the settings the last exact render was made with."""
        if settings != self.exact:
            raise BadRequest(HTTPStatus.CONFLICT, "render these settings exactly before saving")
        path = self.folder / self.track_key / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), indent=1), encoding="utf-8")
        return path

    def presets(self) -> dict[str, dict]:
        """Saved presets by name: only the settings that differ from the defaults."""
        folder = self.folder / "presets"
        out = {}
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        return out

    def save_preset(self, name: str, settings: Settings) -> None:
        """Keep a named preset of what differs from the defaults."""
        if not PRESET_NAME.fullmatch(name) or name.strip() != name or name in (".", ".."):
            raise BadRequest(
                HTTPStatus.BAD_REQUEST, "a preset name is 1-40 letters, digits, spaces"
            )
        defaults = asdict(Settings())
        changed = {k: v for k, v in asdict(settings).items() if defaults[k] != v}
        folder = self.folder / "presets"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.json").write_text(json.dumps(changed, ensure_ascii=False), "utf-8")


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


def _only(body: dict, *keys: str) -> None:
    extra = sorted(set(body) - set(keys))
    if extra:
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"unexpected {', '.join(extra)}")


class Handler(BaseHTTPRequestHandler):
    """Answers the page; the studio and the token sit on the server."""

    server: "StudioServer"  # pyright: ignore[reportIncompatibleVariableOverride]

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the console for the page address and render times."""

    def _send(self, status: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        self.send_response(status)
        for name, value in {**HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # the page drops a request a newer one replaced; its closed socket is not an error
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, given: str | None) -> bool:
        return given is not None and hmac.compare_digest(given.encode(), self.server.token.encode())

    def do_GET(self) -> None:
        """The page, its two static files and the read-only API."""
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "wrong host")
        url = urlsplit(self.path)
        if url.path in ("/", "/index.html"):
            given = parse_qs(url.query).get("t", [None])[0]
            if not self._token_ok(given):
                return self._error(HTTPStatus.FORBIDDEN, "open the address the studio printed")
        if url.path in self.server.files:
            path, kind = self.server.files[url.path]
            return self._send(HTTPStatus.OK, path.read_bytes(), kind)
        if not url.path.startswith("/api/"):
            return self._error(HTTPStatus.NOT_FOUND, "no such page")
        if not self._token_ok(self.headers.get("X-Token")):
            return self._error(HTTPStatus.FORBIDDEN, "no token")
        studio = self.server.studio
        if url.path == "/api/schema":
            return self._json(HTTPStatus.OK, self._schema())
        if url.path == "/api/presets":
            return self._json(HTTPStatus.OK, studio.presets())
        if url.path == "/api/original":
            return self._send(
                HTTPStatus.OK,
                wav_bytes(studio.original, studio.rate),
                "audio/wav",
                {"X-Lufs": f"{studio.original_lufs:.2f}"},
            )
        return self._error(HTTPStatus.NOT_FOUND, "no such call")

    def _schema(self) -> dict:
        studio = self.server.studio
        saved = studio.folder / studio.track_key / "settings.json"
        current = Settings()
        if saved.exists():
            with contextlib.suppress(ValueError):
                current = from_dict(json.loads(saved.read_text(encoding="utf-8")))
        ctx = studio.engine.context
        return {
            **schema(),
            "current": asdict(current),
            "track": {
                "seconds": len(studio.original) / studio.rate,
                "rate": studio.rate,
                "tuning_cents": ctx.tuning_cents,
                "master_lufs": ctx.master_lufs,
                "original_lufs": studio.original_lufs,
                "saved": str(saved),
            },
        }

    def _body(self) -> dict:
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise BadRequest(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")
        if self.headers.get("Transfer-Encoding"):
            raise BadRequest(HTTPStatus.LENGTH_REQUIRED, "no chunked bodies")
        length = self.headers.get("Content-Length", "")
        if not length.isdigit():
            raise BadRequest(HTTPStatus.LENGTH_REQUIRED, "Content-Length is needed")
        if int(length) > MAX_BODY:
            self.close_connection = True
            raise BadRequest(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the body is too large")
        try:
            body = json.loads(self.rfile.read(int(length)))
        except ValueError as error:
            raise BadRequest(HTTPStatus.BAD_REQUEST, "the body is not JSON") from error
        if not isinstance(body, dict):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "the body is a JSON object")
        return body

    def do_POST(self) -> None:
        """Renders, saves and presets."""
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "wrong host")
        if not self._token_ok(self.headers.get("X-Token")):
            return self._error(HTTPStatus.FORBIDDEN, "no token")
        try:
            body = self._body()
            path = urlsplit(self.path).path
            if path == "/api/preview":
                return self._preview(body)
            if path == "/api/render":
                return self._render(body)
            if path == "/api/save":
                _only(body, "settings")
                saved = self.server.studio.save(_settings(body))
                return self._json(HTTPStatus.OK, {"path": str(saved)})
            if path == "/api/preset":
                _only(body, "name", "settings")
                name = body.get("name")
                if not isinstance(name, str):
                    raise BadRequest(HTTPStatus.BAD_REQUEST, "a preset needs a name")
                self.server.studio.save_preset(name, _settings(body))
                return self._json(HTTPStatus.OK, {"name": name})
            return self._error(HTTPStatus.NOT_FOUND, "no such call")
        except BadRequest as error:
            return self._error(error.status, str(error))

    def _preview(self, body: dict) -> None:
        _only(body, "settings", "seq", "start", "end")
        studio = self.server.studio
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
            return self._error(HTTPStatus.CONFLICT, "a newer request came in")
        took = (time.monotonic() - began) * 1000
        print(f"preview {end - start:.1f} s in {took:.0f} ms")  # type: ignore[operator]
        lufs = integrated_lufs(studio.original[first:last], studio.rate)
        self._send(
            HTTPStatus.OK,
            wav_bytes(samples, studio.rate),
            "audio/wav",
            {
                "X-Render-Ms": f"{took:.0f}",
                "X-Lufs": f"{integrated_lufs(samples, studio.rate):.2f}",
                "X-Original-Lufs": f"{lufs:.2f}",
            },
        )

    def _render(self, body: dict) -> None:
        _only(body, "settings", "seq")
        studio = self.server.studio
        settings, seq = _settings(body), _seq(body)
        began = time.monotonic()
        rendered = studio.render(settings, seq)
        if rendered is None:
            return self._error(HTTPStatus.CONFLICT, "a newer request came in")
        took = (time.monotonic() - began) * 1000
        print(f"exact render in {took:.0f} ms")
        self._send(
            HTTPStatus.OK,
            wav_bytes(rendered.final, studio.rate),
            "audio/wav",
            {
                "X-Render-Ms": f"{took:.0f}",
                "X-Lufs": f"{integrated_lufs(rendered.final, studio.rate):.2f}",
            },
        )


class StudioServer(ThreadingHTTPServer):
    """The HTTP server with the studio and its token."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], studio: Studio, token: str) -> None:
        """Bind and keep the studio."""
        super().__init__(address, Handler)
        self.studio = studio
        self.token = token
        self.files = site_files()


def make_server(studio: Studio, token: str, port: int = 0) -> StudioServer:
    """A server on 127.0.0.1 only; port 0 takes a free one."""
    return StudioServer(("127.0.0.1", port), studio, token)
