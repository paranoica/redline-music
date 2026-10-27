import itertools

import numpy as np
import pytest
from redline_signals import make_rng

from redline.chain import (
    DIRT_LEVELS,
    GAIN_LIMIT_DB,
    Compressor,
    DirtLevel,
    Target,
    check_vocal,
    curve_distance,
    eq_matrix,
    process_vocal,
    render_dirt,
    solve_gains,
)
from redline.loudness import gated_crest_db, integrated_lufs
from redline.spectrum import tonal_curve

RATE = 44100
UPPER = 16000.0
LEVELS = {level.name: level for level in DIRT_LEVELS}


def pink(seconds=6.0, tilt_above_3k_db=0.0):
    rng = make_rng()
    n = int(seconds * RATE)
    spec = np.fft.rfft(rng.standard_normal((n, 2)), axis=0)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    shape = 1 / np.sqrt(np.maximum(freqs, 20.0))
    shape[freqs > 3000] *= 10 ** (tilt_above_3k_db / 20)
    x = np.fft.irfft(spec * shape[:, None], n=n, axis=0)
    return (x / np.abs(x).max() * 0.3).astype(np.float32)


def target_of(samples):
    curve = tonal_curve(samples, RATE, UPPER)
    return Target(curve.centers_hz, curve.curve_db)


def speechlike(seconds=6.0, floor=0.0):
    rng = make_rng()
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    envelope = floor + (1 - floor) * np.clip(np.sin(2 * np.pi * 4 * t), 0, None) ** 2
    mono = rng.standard_normal(n) * envelope * 0.2
    return np.column_stack([mono, mono]).astype(np.float32)


def test_eq_brings_the_curve_toward_the_target():
    target = target_of(pink())
    source = pink(tilt_above_3k_db=6.0)
    before = curve_distance(tonal_curve(source, RATE, UPPER), target)
    out, chain = process_vocal(source, RATE, LEVELS["eq"], target, UPPER)
    after = curve_distance(tonal_curve(out, RATE, UPPER), target)
    assert after <= 0.3 * before
    assert chain.passes_error_db[0] == pytest.approx(before, rel=0.01)


def test_eq_stays_flat_when_the_curve_already_matches():
    source = pink()
    _, chain = process_vocal(source, RATE, LEVELS["eq"], target_of(source), UPPER)
    assert np.all(np.abs(chain.eq_gains_db) <= 0.5)


def test_eq_ignores_the_bands_under_the_fit_range():
    source = pink()
    curve = tonal_curve(source, RATE, UPPER)
    cut = tuple(
        db - (20.0 if hz < 200 else 0.0)
        for hz, db in zip(curve.centers_hz, curve.curve_db, strict=True)
    )
    _, chain = process_vocal(source, RATE, LEVELS["eq"], Target(curve.centers_hz, cut), UPPER)
    assert np.all(np.abs(chain.eq_gains_db) <= 0.5)


def test_compression_keeps_the_level_going_into_the_drive():
    x = speechlike(floor=0.3)
    level = DirtLevel("compressor only", Compressor(-30.0, 10.0, 2.0, 60.0), 0.0, None)
    out, _ = render_dirt(x, RATE, level)
    assert gated_crest_db(out, RATE) < gated_crest_db(x, RATE) - 1
    assert integrated_lufs(out, RATE) == pytest.approx(integrated_lufs(x, RATE), abs=0.1)


def test_eq_gains_stop_at_the_limit():
    source = pink()
    curve = tonal_curve(source, RATE, UPPER)
    boosted = tuple(
        db + (30.0 if hz > 2000 else 0.0)
        for hz, db in zip(curve.centers_hz, curve.curve_db, strict=True)
    )
    target = Target(curve.centers_hz, boosted)
    diff = np.array(boosted) - np.array(curve.curve_db)
    matrix, used = eq_matrix(curve.centers_hz, RATE)
    assert solve_gains(diff[used], matrix).max() > GAIN_LIMIT_DB
    _, chain = process_vocal(source, RATE, LEVELS["eq"], target, UPPER)
    assert max(chain.eq_gains_db) == pytest.approx(GAIN_LIMIT_DB)
    assert min(chain.eq_gains_db) >= -GAIN_LIMIT_DB


def test_dirt_flattens_peaks_level_by_level():
    # the dips stay above the expander's gate, which deepens gaps and would raise the crest
    x = speechlike(floor=0.3)
    target = target_of(x)
    crests = [gated_crest_db(x, RATE)]
    for name in ("light", "medium", "heavy"):
        out, _ = process_vocal(x, RATE, LEVELS[name], target, UPPER)
        crests.append(gated_crest_db(out, RATE))
    assert all(a > b for a, b in itertools.pairwise(crests)), crests


def clipped_share(samples, level):
    return float(np.mean(np.abs(samples) >= level * 10 ** (-0.5 / 20)))


def test_clipping_grows_from_light_to_heavy():
    x = speechlike()
    shares = []
    for name in ("light", "medium", "heavy"):
        out, clip = render_dirt(x, RATE, LEVELS[name])
        shares.append(clipped_share(out, clip if clip is not None else np.abs(out).max()))
    assert shares[0] < shares[1] < shares[2], shares


@pytest.mark.parametrize("name", [level.name for level in DIRT_LEVELS])
def test_vocal_keeps_its_loudness(name):
    x = speechlike()
    out, _ = process_vocal(x, RATE, LEVELS[name], target_of(pink()), UPPER)
    assert integrated_lufs(out, RATE) == pytest.approx(integrated_lufs(x, RATE), abs=0.1)


@pytest.mark.parametrize("vocal_lufs", [-20.0, -35.0])
def test_vocal_guard_accepts_a_vocal_up_to_the_boundary(vocal_lufs):
    check_vocal(vocal_lufs, -10.0)


@pytest.mark.parametrize("vocal_lufs", [-35.1, -np.inf, np.nan])
def test_vocal_guard_refuses_residue_and_silence(vocal_lufs):
    with pytest.raises(ValueError, match="no vocal"):
        check_vocal(vocal_lufs, -10.0)
