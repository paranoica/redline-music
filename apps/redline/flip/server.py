"""The flip routes of the song page: /api/flip/*, for the beat the song points at."""

import json
import threading
from dataclasses import dataclass, replace
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import soundfile as sf

from redline.core import native
from redline.core.private import make_private_dir
from redline.flip import model, pipeline
from redline.flip import render as mixer
from redline.flip.score import HATS
from redline.record.takes import file_sha256
from redline.song import model as song_model
from redline.song.jobs import ACTIVE, Busy
from redline.song.server import ApiError, _int, _one
from redline.studio.server import Reply, reply_bytes, reply_json, wav_bytes
from redline.web.local import only

if TYPE_CHECKING:
    from redline.song.server import SongApp

GENERATED = "private/lyrics/generated"
STEMS = ("drums", "bass", "other")
EFFECT_PARAMS = ("low", "high", "default", "unit")


@dataclass(frozen=True)
class Context:
    """What the song's beat means for flips: its source and the folder of its flips."""

    song: song_model.Song
    line: pipeline.Lineage
    folder: Path


def _whole(query: dict[str, list[str]], key: str) -> int | None:
    value = _one(query, key)
    if value is None:
        return None
    if not (value.isascii() and value.isdigit()) or len(value) > 9 or int(value) < 1:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{key} is a whole number from 1")
    return int(value)


def effect_ranges() -> dict[str, dict[str, dict[str, Any]]]:
    """The params of the flip's effects with their ranges, defaults and units."""
    listed = native.rack_effects() or []
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for item in listed:
        name = item["effect"]
        if name not in model.RUST_PARAMS:
            continue
        wanted = model.RUST_PARAMS[name]
        out[name] = {
            p["name"]: {k: p[k] for k in EFFECT_PARAMS if k in p}
            for p in item["params"]
            if p["name"] in wanted
        }
    return out


class FlipRoutes:
    """The flip calls of one song app."""

    def __init__(self, app: "SongApp") -> None:
        """Serve the flips of the beat of `app`'s song."""
        self.app = app
        self._lock = threading.Lock()
        self._peaks: dict[tuple, list[list[float]]] = {}
        self._hashes: dict[tuple, str] = {}

    def context(self) -> Context:
        """The song's beat as a flip source.

        Raises:
            ApiError: 409 own_beat for a seed beat, 409 no_beat when the file or its flip is gone.
        """
        song = self.app.song()
        if "own" in song.beat:
            raise ApiError(HTTPStatus.CONFLICT, "flips are for a beat file", "own_beat")
        try:
            line = pipeline.lineage(song.beat["file"], self.app.root)
            folder = pipeline.flip_dir(self.app.root, line.sha8)
        except pipeline.PipelineError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "no_beat") from None
        return Context(song, line, folder)

    def _analysis(self, ctx: Context, needed: bool = True) -> dict[str, Any] | None:
        try:
            found = pipeline.read_analysis(ctx.folder)
        except pipeline.PipelineError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "no_analysis") from None
        if found is None and needed:
            raise ApiError(
                HTTPStatus.CONFLICT, "the beat is not analysed: run flip_stems", "no_analysis"
            )
        return found

    def _draft(self, ctx: Context) -> dict[str, Any] | None:
        try:
            path = pipeline.inside(ctx.folder, model.DRAFT)
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, pipeline.PipelineError):
            return None

    def _hash(self, path: Path) -> str:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if key not in self._hashes:
            self._hashes[key] = file_sha256(path)
        return self._hashes[key]

    def _over_other_beat(self, ctx: Context, n: int | None) -> int:
        song = ctx.song
        if n is None or "takes" not in song.voice:
            return 0
        try:
            render = pipeline.inside(ctx.folder, f"flip-{n}.wav")
            folder = song_model.resolve_inside(song.voice["takes"], self.app.root, "voice.takes")
            target = self._hash(render)
        except (pipeline.PipelineError, song_model.SongError, OSError):
            return 0
        count = 0
        takes = sorted(folder.iterdir()) if folder.is_dir() else []
        for take in takes:
            if not song_model.TAKE_FILE.fullmatch(take.name):
                continue
            try:
                data = json.loads(take.with_suffix(".json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                isinstance(data, dict)
                and data.get("complete") is True
                and data.get("beat_sha256") != target
            ):
                count += 1
        return count

    # reads

    def state(self, query: dict[str, list[str]]) -> Reply:
        """The source, its analysis, draft, versions and renders."""
        ctx = self.context()
        analysis = self._analysis(ctx, needed=False)
        renders = pipeline.renders(ctx.folder)
        asked = _whole(query, "n")
        n = asked if asked is not None else (renders[-1] if renders else None)
        return reply_json(
            HTTPStatus.OK,
            {
                "source": ctx.line.source,
                "sha8": ctx.line.sha8,
                "analysis": None if analysis is None else pipeline.summary(analysis),
                "draft": self._draft(ctx),
                "versions": pipeline.versions(ctx.folder),
                "renders": renders,
                "used": ctx.line.used,
                "takes_over_other_beat": self._over_other_beat(ctx, n),
                "presets": list(HATS),
                "effects": effect_ranges(),
            },
        )

    def peaks(self, query: dict[str, list[str]]) -> Reply:
        """About a thousand [min, max] pairs of the source or of one stem."""
        ctx = self.context()
        analysis = self._analysis(ctx, needed=False)
        stem = _one(query, "stem")
        try:
            if stem is None:
                path = song_model.resolve_inside(ctx.line.source, self.app.root, "source")
                stat = path.stat()
                key: tuple = (str(path), stat.st_mtime_ns, stat.st_size, None)
                if key not in self._peaks:
                    self._peaks[key] = pipeline.source_waveform(path)
            else:
                if stem not in STEMS:
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"stem is one of {', '.join(STEMS)}")
                self._analysis(ctx)
                made = pipeline.inside(ctx.folder, pipeline.STEMS_DIR, f"{stem}.wav").stat()
                key = (str(ctx.folder), made.st_mtime_ns, made.st_size, stem)
                if key not in self._peaks:
                    self._peaks[key] = pipeline.stem_waveform(ctx.folder, stem)
        except (pipeline.PipelineError, song_model.SongError, OSError) as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "unreadable") from None
        bars = analysis["bar_starts_s"] if analysis and _one(query, "bars") == "1" else []
        return reply_json(HTTPStatus.OK, {"bar_starts_s": bars, "peaks": self._peaks[key]})

    def audio(self, query: dict[str, list[str]]) -> Reply:
        """A render by number, or a stem by name, as a 16-bit WAV."""
        ctx = self.context()
        n, stem = _whole(query, "n"), _one(query, "stem")
        if (n is None) == (stem is None):
            raise ApiError(HTTPStatus.BAD_REQUEST, "give n or stem")
        try:
            if n is not None:
                if n not in pipeline.renders(ctx.folder):
                    raise ApiError(HTTPStatus.NOT_FOUND, f"no render {n}", "no_render")
                path = pipeline.inside(ctx.folder, f"flip-{n}.wav")
            else:
                if stem not in STEMS:
                    raise ApiError(HTTPStatus.BAD_REQUEST, f"stem is one of {', '.join(STEMS)}")
                path = pipeline.inside(ctx.folder, pipeline.STEMS_DIR, f"{stem}.wav")
                if not path.is_file():
                    raise ApiError(HTTPStatus.CONFLICT, "the beat is not analysed", "no_analysis")
            samples, rate = sf.read(path, dtype="float32", always_2d=True)
        except (pipeline.PipelineError, sf.LibsndfileError, OSError) as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "unreadable") from None
        return reply_bytes(
            HTTPStatus.OK, wav_bytes(np.asarray(samples, dtype=np.float32), rate), "audio/wav"
        )

    # writes

    def draft(self, body: dict) -> Reply:
        """Replace the page's working copy after the flip parses and fits the beat."""
        only(body, "flip")
        ctx = self.context()
        try:
            flip = model.parse(body.get("flip"))
            if flip.source != ctx.line.source:
                raise model.FlipError(f"source: must be {ctx.line.source}")
            problem = model.synth_tempo_problem(flip)
            if problem is not None:
                raise model.FlipError(problem)
            analysis = self._analysis(ctx, needed=False)
            if analysis is not None:
                bars = len(mixer.source_bar_starts(flip, analysis["frames"], analysis["rate"])) - 1
                for i, block in enumerate(flip.arrangement):
                    if block.to_bar > bars:
                        raise model.FlipError(
                            f"arrangement[{i}].to_bar: {block.to_bar} is past the {bars} bars"
                        )
        except model.FlipError as error:
            raise ApiError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                str(error),
                "invalid_flip",
                errors=[str(error)],
            ) from None
        make_private_dir(ctx.folder, self.app.root)
        model.save_draft(ctx.folder, flip)
        return reply_json(HTTPStatus.OK, {"ok": True})

    def propose(self, body: dict) -> Reply:
        """An arrangement for the song's chosen text."""
        only(body)
        ctx = self.context()
        text = ctx.song.text
        if text is None:
            raise ApiError(HTTPStatus.CONFLICT, "the song has no text", "no_text")
        self._analysis(ctx)
        from redline.lyrics.write.generate import versions

        folder = song_model.resolve_inside(GENERATED, self.app.root) / str(text["seed"])
        have = versions(folder) if folder.is_dir() else {}
        if text["version"] not in have:
            raise ApiError(HTTPStatus.CONFLICT, "the song's text is missing", "no_text")
        try:
            source = song_model.resolve_inside(ctx.line.source, self.app.root, "source")
            blocks = pipeline.propose_blocks(
                source, ctx.folder, have[text["version"]].read_text(encoding="utf-8")
            )
        except pipeline.PipelineError as error:
            raise ApiError(HTTPStatus.CONFLICT, str(error), "no_text") from None
        return reply_json(HTTPStatus.OK, {"arrangement": blocks})

    def render(self, body: dict) -> Reply:
        """Save the draft as the next version and start its render."""
        only(body)
        with self._lock:
            ctx = self.context()
            self._analysis(ctx)
            running = self.app.jobs.state(self.app.name).get("flip_render")
            if running is not None and running["state"] in ACTIVE:
                raise ApiError(HTTPStatus.CONFLICT, "a flip render is running", "busy")
            try:
                flip = model.load(pipeline.inside(ctx.folder, model.DRAFT))
            except (model.FlipError, pipeline.PipelineError):
                raise ApiError(HTTPStatus.CONFLICT, "the draft is missing", "no_draft") from None
            n = model.save_new(ctx.folder, flip)
            try:
                self.app._start("flip_render", 0, flip=n)
            except Busy as error:
                # a version is made for its render: without one it would only push n on
                (ctx.folder / f"flip-{n}.json").unlink(missing_ok=True)
                raise ApiError(HTTPStatus.CONFLICT, str(error), "busy") from None
            return reply_json(HTTPStatus.ACCEPTED, {"n": n})

    def use(self, body: dict) -> Reply:
        """Make a render the song's beat, as a beat change does."""
        only(body, "n", "rev")
        n, rev = _int(body.get("n"), "n", 1), _int(body.get("rev"), "rev")
        ctx = self.context()
        if n not in pipeline.renders(ctx.folder):
            raise ApiError(HTTPStatus.NOT_FOUND, f"no render {n}", "no_render")
        beat = {"file": f"work/flips/{ctx.line.sha8}/flip-{n}.wav"}
        return self.app._save_beat_change(replace(ctx.song, beat=beat), rev, None)

    # dispatch

    def get(self, path: str, query: dict[str, list[str]]) -> Reply:
        """Answer a GET under /api/flip/."""
        routes = {
            "/api/flip/state": self.state,
            "/api/flip/peaks": self.peaks,
            "/api/flip/audio": self.audio,
        }
        if path in routes:
            return routes[path](query)
        return reply_json(HTTPStatus.NOT_FOUND, {"error": "no such call"})

    def post(self, path: str, body: dict) -> Reply:
        """Answer a POST under /api/flip/."""
        routes = {
            "/api/flip/draft": self.draft,
            "/api/flip/propose": self.propose,
            "/api/flip/render": self.render,
            "/api/flip/use": self.use,
        }
        if path in routes:
            return routes[path](body)
        return reply_json(HTTPStatus.NOT_FOUND, {"error": "no such call"})
