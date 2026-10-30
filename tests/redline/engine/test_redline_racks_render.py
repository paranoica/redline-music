import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from redline.core import native
from redline.core.loudness import integrated_lufs
from redline.engine import live, racks_render
from redline.engine.render import Cancelled, Engine
from redline.engine.settings import Settings, from_dict, to_dict
from redline_signals import RATE, engine_context, engine_stems

redline_dsp = pytest.importorskip("redline_dsp")

HARNESS = Path(__file__).with_name("live_engine.mjs")
# the worklet stays silent while its delay lines fill, then fades in over 384 frames
FADE_IN = 384 + 2 * 128
# the wasm build of the core against the native one: the same code, other float rounding
FLOAT_TOLERANCE = 1e-6


@pytest.fixture(scope="module")
def engine():
    vocal, beat = engine_stems(6.0)
    return Engine(vocal, beat, engine_context(vocal))


def node_with_types():
    node = shutil.which("node")
    if node is None:
        return None
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    major = int(re.match(r"v(\d+)", version.stdout).group(1))
    # the studio's engine is TypeScript, which node runs as it is from 23 on
    return node if major >= 23 else None


def live_engine(tmp_path, vocal, beat, racks, lanes):
    """What the studio's worklet engine plays for these stems and racks, and its latency."""
    node = node_with_types()
    if node is None:
        pytest.skip("needs node 23 or later to run the studio's engine")
    for name, stem in (("vocal", vocal), ("beat", beat)):
        np.ascontiguousarray(stem, dtype="<f4").tofile(tmp_path / f"{name}.f32")
    spec = {
        "rate": RATE,
        "vocal": str(tmp_path / "vocal.f32"),
        "beat": str(tmp_path / "beat.f32"),
        "racks": racks,
        "lanes": [{"slot": s, "param": p, "points": points} for s, p, points in lanes],
    }
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "out.f32"
    done = subprocess.run(
        [node, str(HARNESS), str(tmp_path / "spec.json"), str(out)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    played = np.fromfile(out, dtype="<f4").reshape(-1, 2)
    return played, json.loads(done.stdout)["latency"]


def against_live(offline, played, latency):
    """The largest difference between the offline final and what the worklet played.

    The worklet plays frame f of the track `latency` frames late and stops at the track's
    end, so the last `latency` frames of the offline render are never heard live.
    """
    assert latency < len(played) - FADE_IN
    heard = played[latency + FADE_IN :]
    return float(np.abs(offline[FADE_IN : FADE_IN + len(heard)] - heard).max())


def played_and_rendered(engine, tmp_path, changes):
    settings = from_dict({**to_dict(Settings()), **changes})
    numbers = live.analysis(engine, settings)
    racks = live.racks(settings, numbers)
    rendered = racks_render.render(engine, settings)
    played, latency = live_engine(tmp_path, engine.vocal, engine.beat, racks, [])
    assert latency > 0
    assert np.abs(played).max() > 0.1
    return rendered.final, played, latency


def test_the_render_is_what_the_studio_plays_block_by_block(engine, tmp_path):
    no_layers = {"octave_down_on": False, "doubles_on": False}
    final, played, latency = played_and_rendered(engine, tmp_path, no_layers)
    assert against_live(final, played, latency) <= FLOAT_TOLERANCE


def test_the_default_render_is_what_the_studio_plays_but_for_rounding(engine, tmp_path):
    # the octaver and the doubler shift pitch with signalsmith, whose wasm build rounds its
    # FFTs otherwise than the native one; a frame off would leave a residual near -20 dB
    final, played, latency = played_and_rendered(engine, tmp_path, {})
    heard = played[latency + FADE_IN :]
    offline = final[FADE_IN : FADE_IN + len(heard)]
    residual = np.mean(np.square(offline - heard)) / np.mean(np.square(offline))
    assert 10 * np.log10(residual) <= -80


def side_and_lane_racks():
    # the beat rack is late by its 808 drive; the vocal rack ducks under the beat behind a
    # limiter's lookahead, then a gain steps down on a 512-frame boundary
    step = 512 * 64 / RATE
    racks = {
        "beat": json.dumps(
            {"effects": [{"effect": "bass_drive", "params": {"freq": 120, "drive": 12}}]}
        ),
        "vocal": json.dumps(
            {
                "effects": [
                    {"effect": "limiter", "params": {"ceiling": -3, "lookahead": 5}},
                    {"effect": "duck", "params": {"threshold": -30, "depth": 18, "attack": 1}},
                    {"effect": "gain", "params": {"db": 0}},
                ]
            }
        ),
        "master": json.dumps({"effects": [{"effect": "gain", "params": {"db": -6}}]}),
    }
    lanes = [(2, "db", [(0.0, 0.0), (step - 1e-6, 0.0), (step, -20.0)])]
    return racks, lanes


def test_a_side_and_a_lane_behind_latency_reach_the_racks_as_the_studio_plays_them(
    engine, tmp_path
):
    racks, lanes = side_and_lane_racks()
    _, _, final = racks_render.stream(engine.vocal, engine.beat, RATE, racks, lanes)
    played, latency = live_engine(tmp_path, engine.vocal, engine.beat, racks, lanes)
    alignment = native.rack_alignment(racks["beat"], RATE)
    assert alignment is not None and alignment[1] > 0
    assert latency > alignment[1]
    assert against_live(final, played, latency) <= FLOAT_TOLERANCE


def test_the_stream_lines_the_stems_up_and_sums_them_into_the_master(engine):
    racks, lanes = side_and_lane_racks()
    vocal, beat, final = racks_render.stream(engine.vocal, engine.beat, RATE, racks, lanes)
    assert vocal.shape == beat.shape == final.shape == engine.vocal.shape
    # the master here is a plain -6 dB: the final is the lined-up stems' sum, frame for frame
    np.testing.assert_allclose(final, (vocal + beat) * 10 ** (-6 / 20), atol=1e-6)
    # the beat after its drive starts on the beat's first frame, not 20 frames late
    alone = native.rack_process(engine.beat, RATE, racks["beat"])
    np.testing.assert_array_equal(beat, alone)


def test_the_racks_render_reads_within_half_a_lu_of_the_classic_engine(engine):
    settings = Settings()
    classic = engine.render(settings)
    rendered = racks_render.render(engine, settings)
    racks_lufs = integrated_lufs(rendered.final, RATE)
    classic_lufs = integrated_lufs(classic.final, RATE)
    print(f"classic {classic_lufs:.3f} LUFS, racks {racks_lufs:.3f} LUFS")
    assert racks_lufs == pytest.approx(classic_lufs, abs=0.5)
    assert rendered.final.shape == classic.final.shape
    # not the classic render under another name
    assert np.abs(rendered.final - classic.final).max() > 1e-3
    assert rendered.chain == classic.chain
    assert rendered.master is not None and classic.master is not None
    assert rendered.master.target_lufs == classic.master.target_lufs
    assert rendered.master.gain_db == pytest.approx(classic.master.gain_db, abs=1e-6)
    assert rendered.master.reached
    assert 20 * np.log10(np.abs(rendered.final).max()) <= -0.1 + 1e-4


def test_with_the_master_off_the_final_is_the_stems_sum(engine):
    settings = from_dict({**to_dict(Settings()), "master_on": False})
    rendered = racks_render.render(engine, settings)
    assert rendered.master is None
    np.testing.assert_array_equal(rendered.final, rendered.vocal + rendered.beat)


def test_a_cancelled_render_stops(engine):
    with pytest.raises(Cancelled):
        racks_render.render(engine, Settings(), lambda: True)


def test_without_the_rust_module_the_render_points_to_classic(engine, monkeypatch):
    monkeypatch.setattr(native, "redline_dsp", None)
    racks, lanes = side_and_lane_racks()
    with pytest.raises(ValueError, match="--classic"):
        racks_render.stream(engine.vocal, engine.beat, RATE, racks, lanes)
