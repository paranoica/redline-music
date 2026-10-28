import itertools

import numpy as np
import pytest
from redline_signals import make_rng, tone

from redline.loudness import integrated_lufs
from redline.master import CEILING_DB, MAX_GAIN_DB, crossover, master_clip, saturate_low
from redline.spectrum import low_end

RATE = 44100


def spectrum_db(x):
    return 10 * np.log10(np.abs(np.fft.rfft(x[:, 0])) ** 2 + 1e-30)


def test_crossover_bands_sum_to_a_flat_magnitude():
    impulse = np.zeros((RATE, 2), dtype=np.float32)
    impulse[0] = 1.0
    low, high = crossover(impulse, RATE, 120.0)
    freqs = np.fft.rfftfreq(RATE, 1 / RATE)
    band = (freqs > 20) & (freqs < 20000)
    # an LR4 pair sums to an allpass: the summed response is flat in magnitude
    assert np.max(np.abs(spectrum_db(low + high)[band])) < 0.1


def band_level_db(x, freq):
    spec = np.abs(np.fft.rfft(x[:, 0] * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / RATE)
    return 20 * np.log10(spec[np.abs(freqs - freq) <= 2].max())


def test_crossover_splits_low_from_high():
    x = tone(30, 2, amp=0.3) + tone(1000, 2, amp=0.3)
    low, high = crossover(x, RATE, 120.0)
    assert band_level_db(x, 1000) - band_level_db(low, 1000) >= 40
    assert band_level_db(x, 30) - band_level_db(high, 30) >= 30
    assert band_level_db(low, 30) == pytest.approx(band_level_db(x, 30), abs=0.5)


def beatlike(seconds=6.0):
    rng = make_rng()
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 0.5) / 0.15))
    hats = rng.standard_normal(n) * np.exp(-((t % 0.25) / 0.02)) * 0.2
    mono = 0.5 * kick + hats
    return np.column_stack([mono, mono]).astype(np.float32)


@pytest.mark.parametrize("target", [-14.0, -9.0, -6.0])
def test_master_clip_reaches_the_target_loudness(target):
    out, result = master_clip(beatlike(), RATE, target)
    assert integrated_lufs(out, RATE) == pytest.approx(target, abs=0.2)
    assert 20 * np.log10(np.abs(out).max()) <= CEILING_DB + 1e-3
    assert result.reached


def test_master_clip_below_the_input_level_only_turns_down():
    x = beatlike()
    target = integrated_lufs(x, RATE) - 6.0
    out, result = master_clip(x, RATE, target)
    assert result.gain_db == pytest.approx(-6.0, abs=0.05)
    np.testing.assert_allclose(out, x * 10 ** (-6 / 20), atol=1e-4)


def test_master_clip_reports_an_unreachable_target():
    out, result = master_clip(beatlike(), RATE, 3.0)
    assert not result.reached
    assert integrated_lufs(out, RATE) < 3.0
    assert 20 * np.log10(np.abs(out).max()) <= CEILING_DB + 1e-3


def test_master_clip_keeps_aliasing_down():
    x = tone(3000, 2, amp=0.1)
    # 26 dB over the sine's own loudness puts its plain peak at 2.0: it has to clip
    out, result = master_clip(x, RATE, integrated_lufs(x, RATE) + 26.0)
    assert result.clipped_share > 0.1
    seg = out[RATE // 2 : RATE // 2 + RATE, 0]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    freqs = np.fft.rfftfreq(len(seg), 1 / RATE)
    harmonic = np.zeros_like(freqs, dtype=bool)
    for k in range(8):
        harmonic |= np.abs(freqs - k * 3000) <= 50
    fundamental = spec[np.abs(freqs - 3000) <= 50].sum()
    assert 10 * np.log10(spec[~harmonic].sum() / fundamental) < -50


def test_master_clip_tames_a_mix_already_over_full_scale():
    x = beatlike() * 4.0
    target = integrated_lufs(x, RATE) - 3.0
    out, _ = master_clip(x, RATE, target)
    assert 20 * np.log10(np.abs(out).max()) <= CEILING_DB + 1e-3
    assert integrated_lufs(out, RATE) == pytest.approx(target, abs=0.2)


def test_master_clip_refuses_silence():
    with pytest.raises(ValueError, match="silent"):
        master_clip(np.zeros((RATE * 4, 2), dtype=np.float32), RATE, -9.0)


def test_master_clip_reports_a_target_it_misses_after_the_clean_render():
    # the base-rate clip reaches about -1 LUFS at +30 dB, the clean render far less
    out, result = master_clip(beatlike(), RATE, -2.0)
    assert not result.reached
    assert integrated_lufs(out, RATE) < -2.5


def test_master_clip_caps_the_plain_gain():
    x = tone(440, 4, amp=1e-3)
    out, result = master_clip(x, RATE, -14.0)
    assert result.gain_db == pytest.approx(MAX_GAIN_DB)
    assert not result.reached
    np.testing.assert_allclose(out, x * 10 ** (MAX_GAIN_DB / 20), rtol=1e-5)


def test_master_clip_keeps_its_turn_down_small():
    # repeated clip and band limit at the high rate leave little for the decimation to ring
    _, result = master_clip(beatlike(), RATE, -6.0)
    assert result.trim_db > -1.5


def low_rms_db(x):
    from redline.spectrum import low_band

    return 10 * np.log10(np.mean(low_band(x, RATE) ** 2))


def test_saturate_low_without_drive_only_passes_the_crossover():
    x = beatlike()
    low, high = crossover(x, RATE, 120.0)
    np.testing.assert_allclose(saturate_low(x, RATE, 120.0, 0.0), low + high, atol=1e-6)


def test_saturate_low_squashes_the_808_more_with_more_drive():
    x = beatlike()
    crests = [low_end(saturate_low(x, RATE, 120.0, d), RATE).crest_db for d in (0, 6, 12, 18)]
    assert all(a > b for a, b in itertools.pairwise(crests)), crests


def test_saturate_low_keeps_the_sub_level():
    x = beatlike()
    for drive_db in (6.0, 18.0):
        assert low_rms_db(saturate_low(x, RATE, 120.0, drive_db)) == pytest.approx(
            low_rms_db(x), abs=0.5
        )


def test_saturate_low_leaves_the_highs_alone():
    x = tone(55, 3, amp=0.4) + tone(2000, 3, amp=0.1)
    y = saturate_low(x, RATE, 120.0, 18.0)
    assert band_level_db(y, 2000) == pytest.approx(band_level_db(x, 2000), abs=0.2)
