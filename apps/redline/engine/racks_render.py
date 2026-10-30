"""The exact render through the vocal, beat and master racks, as the studio's page plays them.

The page's worklet (`apps/studio/src/engine/core.ts`) runs the beat rack, hands its output
to the vocal rack as the side signal, lines the two up by their latencies, sums them and
runs the master rack. `stream` does the same over whole stems with `redline_dsp`, so a
render is what the page played from the start. The numbers a stream cannot measure still
come from `live.analysis`, which renders through the Python engine.
"""

import json
from collections.abc import Callable

import numpy as np
import numpy.typing as npt

from redline.core import native
from redline.core.loudness import integrated_lufs
from redline.engine import live
from redline.engine.render import Cancelled, Engine, Rendered
from redline.engine.settings import Settings
from redline.master.clip import CEILING_DB, REACH_TOLERANCE_LU, MasterResult

Samples = npt.NDArray[np.floating]

NO_RUST = (
    "the render through the racks needs the Rust module redline_dsp, which is not built "
    "here; --classic renders with the Python engine"
)


def _latency(rack_json: str, rate: int) -> int:
    alignment = native.rack_alignment(rack_json, rate)
    if alignment is None:
        raise ValueError(NO_RUST)
    return alignment[1]


def _as_played(rack_json: str, rate: int, lanes: list[native.Lane]) -> list[native.Lane]:
    """Lanes moved so the native rack reads them as the page does.

    The page sets a lane's value at the time of the block's input; the native rack reads
    it later by the latency of the effects ahead of the slot, so the points come earlier
    by as much.
    """
    effects = json.loads(rack_json)["effects"]
    out = []
    for slot, param, points in lanes:
        ahead = _latency(json.dumps({"effects": effects[:slot]}), rate) / rate
        out.append((slot, param, [(t - ahead, v) for t, v in points]))
    return out


def _delayed(samples: Samples, frames: int) -> Samples:
    if frames == 0:
        return samples
    out = np.zeros_like(samples)
    out[frames:] = samples[: len(samples) - frames]
    return out


def stream(
    vocal: Samples,
    beat: Samples,
    rate: int,
    racks: dict[str, str],
    lanes: list[native.Lane],
) -> tuple[Samples, Samples, Samples]:
    """The stems through their racks and the master, lined up on the track's frames.

    Args:
        vocal: The vocal stem, (n, 2).
        beat: The beat stem, the same shape.
        rate: The sample rate in Hz.
        racks: The vocal, beat and master racks, as `live.racks` gives them.
        lanes: The vocal rack's automation, as `live.vocal_lanes` gives it.

    Returns:
        The vocal and the beat after their racks, and the master's output of their sum.

    Raises:
        ValueError: The Rust module is not built, or a rack or lane is wrong.
    """
    beat_out = native.rack_process(beat, rate, racks["beat"])
    if beat_out is None:
        raise ValueError(NO_RUST)
    # the page's vocal rack hears the beat rack's output as it comes out, late by its latency
    side = _delayed(beat_out, _latency(racks["beat"], rate))
    if lanes:
        played = _as_played(racks["vocal"], rate, lanes)
        vocal_out = native.rack_process_automated(vocal, rate, racks["vocal"], played, side)
    else:
        vocal_out = native.rack_process(vocal, rate, racks["vocal"], side)
    if vocal_out is None:
        raise ValueError(NO_RUST)
    final = native.rack_process(vocal_out + beat_out, rate, racks["master"])
    if final is None:
        raise ValueError(NO_RUST)
    return vocal_out, beat_out, final


def render(
    engine: Engine, settings: Settings, cancelled: Callable[[], bool] = lambda: False
) -> Rendered:
    """The whole track at these settings through the racks the page plays.

    The Python engine's render comes first, for the analysis and the chain's record; it is
    cached, so a classic render of the same settings after it is free.

    Raises:
        Cancelled: cancelled said so between stages.
        ValueError: The Rust module is not built, or the racks cannot hold these settings.
    """
    classic = engine.render(settings, cancelled)
    numbers = live.analysis(engine, settings)
    if cancelled():
        raise Cancelled
    try:
        racks = live.racks(settings, numbers)
    except ValueError as error:
        raise ValueError(f"{error}; --classic renders these settings") from error
    lanes = live.vocal_lanes(settings, numbers)
    rate = engine.context.rate
    vocal, beat, final = stream(engine.vocal, engine.beat, rate, racks, lanes)
    if cancelled():
        raise Cancelled
    master = None
    if classic.master is not None:
        master = _master(classic.master, vocal + beat, final, rate, json.loads(racks["master"]))
    return Rendered(vocal, beat, final, classic.chain, master)


def _master(
    planned: MasterResult, mix: Samples, final: Samples, rate: int, rack: dict
) -> MasterResult:
    """What the master effect did to this mix, at the gain and trim the analysis gave it."""
    (effect,) = [e for e in rack["effects"] if e["effect"] == "master"]
    gain, trim = effect["params"]["gain"], effect["params"]["trim"]
    ceiling = 10 ** (CEILING_DB / 20)
    reached = abs(integrated_lufs(final, rate) - planned.target_lufs) <= REACH_TOLERANCE_LU
    clipped = float(np.mean(np.abs(mix * 10 ** (gain / 20)) > ceiling))
    return MasterResult(planned.target_lufs, gain + trim, reached, clipped, trim)
