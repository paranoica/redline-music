"""One measuring run: decode, separate, measure every input, compare, write the report."""

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from redline.audio import Audio, Decoded, decode, hot_master, to_analysis
from redline.compare import disagreement, row_verdicts
from redline.dynamics import low_end_kurtosis, tail_decay, waveform_kurtosis
from redline.loudness import integrated_lufs, measure_loudness
from redline.pitch import estimate_key, measure_pitch, tempo_candidates
from redline.report import write_report
from redline.separate import Separator, Stems, cache_key, separate
from redline.spectrum import low_end, stereo_width, tonal_curve

METRICS_VERSION = "1"
MIN_TAILS = 5
log = logging.getLogger(__name__)


@dataclass
class FileResult:
    """Everything measured on one input file."""

    name: str
    codec: str
    native_rate: int
    cached: bool
    values: dict[str, float | None] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _stem_values(prefix: str, samples: Any, rate: int, upper: float, side_upper: float) -> dict:
    loud = measure_loudness(samples, rate)
    width = stereo_width(samples, rate, min(upper, side_upper))
    values = {
        f"{prefix}.lufs": loud.integrated_lufs,
        f"{prefix}.crest_db": loud.crest_db,
        f"{prefix}.tilt_db_per_oct": tonal_curve(samples, rate, upper).tilt_db_per_oct,
        f"{prefix}.side_mid_low_db": width.side_mid_db[0],
        f"{prefix}.side_mid_mid_db": width.side_mid_db[1],
        f"{prefix}.correlation": width.correlation,
    }
    info = {
        f"{prefix}.lra_lu": loud.lra_lu,
        f"{prefix}.short_term_spread_lu": loud.short_term_spread_lu,
    }
    return {"values": values, "info": info}


def _measure(decoded: Decoded, audio: Audio, stems: Stems, upper: float, side_upper: float) -> dict:
    rate = audio.rate
    hot = hot_master(decoded)
    mix = _stem_values("mix", audio.samples, rate, upper, side_upper)
    vocal = _stem_values("vocal", stems.vocal, rate, upper, side_upper)
    beat = _stem_values("beat", stems.beat, rate, upper, side_upper)
    pitch = measure_pitch(stems.vocal, rate)
    tails_rate, tails = tail_decay(stems.vocal, rate)
    low = low_end(stems.beat, rate)
    lufs = mix["values"]["mix.lufs"]
    values = {**mix["values"], **vocal["values"], **beat["values"]}
    values.update(
        {
            "mix.plr_db": hot.true_peak_db - lufs if math.isfinite(lufs) else None,
            "mix.hot_share": None if hot.lossy else hot.hot_share,
            "mix.duration_s": len(audio.samples) / rate,
            "mix.band_limit_hz": audio.band_limit_hz,
            "vocal.short_term_spread_lu": vocal["info"]["vocal.short_term_spread_lu"],
            "vocal.kurtosis": waveform_kurtosis(stems.vocal, rate),
            "vocal.share_within_10_cents": pitch.share_within_10_cents,
            "vocal.median_deviation_cents": pitch.median_deviation_cents,
            "vocal.stable_share": pitch.stable_share,
            "vocal.tail_decay_db_per_s": tails_rate if tails >= MIN_TAILS else None,
            "pair.vocal_minus_beat_lu": integrated_lufs(stems.vocal, rate)
            - integrated_lufs(stems.beat, rate),
            "beat.low_share_db": low.share_db,
            "beat.low_crest_db": low.crest_db,
            "beat.low_kurtosis": low_end_kurtosis(stems.beat, rate),
        }
    )
    info = {
        **mix["info"],
        **vocal["info"],
        **beat["info"],
        "mix.sample_peak_db": hot.sample_peak_db,
        "mix.true_peak_db": hot.true_peak_db,
        "mix.tempo_candidates": list(tempo_candidates(audio.samples, rate)),
        "mix.key": estimate_key(audio.samples, rate),
        "vocal.voiced_frames": pitch.voiced_frames,
        "vocal.tuning_cents": pitch.tuning_cents,
        "vocal.tuning_assumed": pitch.tuning_assumed,
        "vocal.tails": tails,
        "vocal.tail_decay_all_db_per_s": tails_rate,
    }
    return {
        "values": {k: _finite(v) for k, v in values.items()},
        "info": {k: _finite(v) if isinstance(v, float) else v for k, v in info.items()},
    }


def _metrics_cached(
    cache_dir: Path, key: str, upper: float, side_upper: float, compute: Any
) -> tuple[dict, bool]:
    name = hashlib.sha256(f"{key}|{upper}|{side_upper}|{METRICS_VERSION}".encode()).hexdigest()
    path = cache_dir / "metrics" / f"{name}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8")), True
    result = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    staging.replace(path)
    return result, False


def _new_run_dir(runs: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    runs.mkdir(parents=True, exist_ok=True)
    for attempt in range(100):
        folder = runs / (stamp if attempt == 0 else f"{stamp}-{attempt}")
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder
    raise RuntimeError(f"cannot create a run folder under {runs}")


def measure_run(track: Path, refs: list[Path], work_dir: Path, separator: Separator) -> Path:
    """Measure the track and the references; return the run folder with the report."""
    decoded = [decode(p) for p in [track, *refs]]
    analysed = [to_analysis(d) for d in decoded]
    limits = [a.band_limit_hz for a in analysed if a.band_limit_hz is not None]
    side_limits = [a.side_band_limit_hz for a in analysed if a.side_band_limit_hz is not None]
    upper = min(limits, default=analysed[0].rate / 2)
    side_upper = min(side_limits, default=upper)
    cache_dir = work_dir / "cache"
    results = []
    for path, dec, audio in zip([track, *refs], decoded, analysed, strict=True):
        log.info("measuring %s", path.name)
        stems = separate(audio, separator, cache_dir)
        measured, from_cache = _metrics_cached(
            cache_dir,
            cache_key(audio, separator.id),
            upper,
            side_upper,
            lambda dec=dec, audio=audio, stems=stems: _measure(
                dec, audio, stems, upper, side_upper
            ),
        )
        results.append(
            FileResult(
                path.name,
                dec.codec,
                dec.rate,
                stems.cached and from_cache,
                measured["values"],
                measured["info"],
            )
        )
    track_result, ref_results = results[0], results[1:]
    rows = row_verdicts(track_result.values, [r.values for r in ref_results])
    warning = disagreement(track_result.values, [r.values for r in ref_results])
    run_dir = _new_run_dir(work_dir / "runs")
    payload = {
        "compared_up_to_hz": upper,
        "separator": separator.id,
        "track": track_result.__dict__,
        "refs": [r.__dict__ for r in ref_results],
    }
    (run_dir / "measurements.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    write_report(run_dir / "report.md", track_result, ref_results, rows, warning, upper)
    return run_dir
