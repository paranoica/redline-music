"""The studio: a page on 127.0.0.1 that plays the track and re-renders it as settings move.

One render runs at a time and the newest request wins: an older one stops between stages.
The guards of the page's server live in `redline.web.local`.
"""

import contextlib
import io
import json
import math
import os
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict
from http import HTTPStatus
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core.loudness import integrated_lufs
from redline.engine.render import Cancelled, Engine, Rendered
from redline.engine.settings import PRESET_NAME, Settings, from_dict, schema
from redline.web.local import BadRequest, LocalHandler, LocalServer, only, site_files

Samples = npt.NDArray[np.floating]

# the page lives next to the package, in apps/studio-web
SITE = Path(__file__).resolve().parents[2] / "studio-web"


def wav_bytes(samples: Samples, rate: int) -> bytes:
    """16-bit WAV in memory: enough to listen to, half the bytes of float."""
    buffer = io.BytesIO()
    sf.write(buffer, np.clip(samples, -1.0, 1.0), rate, format="WAV", subtype="PCM_16")
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
    ) -> None:
        """Hold the engine of one track.

        Args:
            engine: The track's engine.
            original: The track as Suno made it, for A/B.
            folder: Where settings and presets are saved.
            track_key: The track's folder name under folder.
            references: Name, samples and rate of each reference, played in the A/B.
        """
        self.references = [
            (name, samples, rate, integrated_lufs(samples, rate))
            for name, samples, rate in references
        ]
        self.engine = engine
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
        rendered = self._run(seq, lambda cancelled: self.engine.render(settings, cancelled))
        if rendered is not None:
            self.exact.add(settings)
        return rendered

    def preview(self, settings: Settings, seq: int, start: int, end: int) -> Samples | None:
        """The loop at these settings, rendered on the loop alone; None when superseded."""
        return self._run(
            seq, lambda _: self.engine.preview(settings, start, end), self._preview_lock
        )

    def save(self, settings: Settings) -> Path:
        """Write the settings the last exact render was made with."""
        if settings not in self.exact:
            raise BadRequest(HTTPStatus.CONFLICT, "render these settings exactly before saving")
        path = self.folder / self.track_key / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace(path, json.dumps(asdict(settings), indent=1))
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
        defaults = asdict(Settings())
        changed = {k: v for k, v in asdict(settings).items() if defaults[k] != v}
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


class Handler(LocalHandler):
    """Answers the page; the studio and the token sit on the server."""

    server: "StudioServer"  # pyright: ignore[reportIncompatibleVariableOverride]

    def api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """Schema, presets, the original and the references."""
        studio = self.server.studio
        if path == "/api/schema":
            return self.json(HTTPStatus.OK, self._schema())
        if path == "/api/presets":
            return self.json(HTTPStatus.OK, studio.presets())
        if path == "/api/references":
            return self.json(
                HTTPStatus.OK,
                [
                    {"index": i, "name": name, "seconds": len(x) / rate, "lufs": lufs}
                    for i, (name, x, rate, lufs) in enumerate(studio.references)
                ],
            )
        if path == "/api/reference":
            return self._reference(query.get("i", []))
        if path == "/api/reference_span":
            return self._reference_span(query)
        if path == "/api/original":
            return self.send(
                HTTPStatus.OK,
                wav_bytes(studio.original, studio.rate),
                "audio/wav",
                {"X-Lufs": f"{studio.original_lufs:.2f}"},
            )
        return self.error(HTTPStatus.NOT_FOUND, "no such call")

    def _which(self, asked: list[str]) -> int | None:
        given = asked[0] if len(asked) == 1 else ""
        if not (given.isascii() and given.isdigit() and len(given) < 4):
            return None
        return int(given) if int(given) < len(self.server.studio.references) else None

    def _reference_span(self, query: dict[str, list[str]]) -> None:
        which = self._which(query.get("i", []))
        try:
            start, end = float(query["start"][0]), float(query["end"][0])
        except (KeyError, ValueError):
            return self.error(HTTPStatus.BAD_REQUEST, "start and end are seconds")
        if which is None:
            return self.error(HTTPStatus.BAD_REQUEST, "no such reference")
        _, samples, rate, _ = self.server.studio.references[which]
        if not (
            math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= len(samples) / rate
        ):
            return self.error(HTTPStatus.BAD_REQUEST, "the span lies outside the reference")
        lufs = integrated_lufs(samples[int(start * rate) : int(end * rate)], rate)
        return self.json(HTTPStatus.OK, {"lufs": lufs if math.isfinite(lufs) else None})

    def _reference(self, asked: list[str]) -> None:
        which = self._which(asked)
        if which is None:
            return self.error(HTTPStatus.BAD_REQUEST, "no such reference")
        _, samples, rate, lufs = self.server.studio.references[which]
        return self.send(
            HTTPStatus.OK, wav_bytes(samples, rate), "audio/wav", {"X-Lufs": f"{lufs:.2f}"}
        )

    def _schema(self) -> dict:
        studio = self.server.studio
        saved = studio.folder / studio.track_key / "settings.json"
        current = Settings()
        if saved.exists():
            with contextlib.suppress(ValueError):
                data = json.loads(saved.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    current = from_dict(data)
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

    def api_post(self, path: str, body: dict) -> None:
        """Renders, saves and presets."""
        if path == "/api/preview":
            return self._preview(body)
        if path == "/api/render":
            return self._render(body)
        if path == "/api/save":
            only(body, "settings")
            saved = self.server.studio.save(_settings(body))
            return self.json(HTTPStatus.OK, {"path": str(saved)})
        if path == "/api/preset":
            only(body, "name", "settings")
            name = body.get("name")
            if not isinstance(name, str):
                raise BadRequest(HTTPStatus.BAD_REQUEST, "a preset needs a name")
            self.server.studio.save_preset(name, _settings(body))
            return self.json(HTTPStatus.OK, {"name": name})
        return self.error(HTTPStatus.NOT_FOUND, "no such call")

    def _preview(self, body: dict) -> None:
        only(body, "settings", "seq", "start", "end")
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
            return self.error(HTTPStatus.CONFLICT, "a newer request came in")
        took = (time.monotonic() - began) * 1000
        print(f"preview {end - start:.1f} s in {took:.0f} ms")  # type: ignore[operator]
        lufs = integrated_lufs(studio.original[first:last], studio.rate)
        self.send(
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
        only(body, "settings", "seq")
        studio = self.server.studio
        settings, seq = _settings(body), _seq(body)
        began = time.monotonic()
        rendered = studio.render(settings, seq)
        if rendered is None:
            return self.error(HTTPStatus.CONFLICT, "a newer request came in")
        took = (time.monotonic() - began) * 1000
        print(f"exact render in {took:.0f} ms")
        self.send(
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

    def __init__(self, address: tuple[str, int], studio: Studio, token: str) -> None:
        """Bind and keep the studio."""
        super().__init__(address, Handler, token, site_files(SITE))
        self.studio = studio


def make_server(studio: Studio, token: str, port: int = 0) -> StudioServer:
    """A server on 127.0.0.1 only; port 0 takes a free one."""
    return StudioServer(("127.0.0.1", port), studio, token)
