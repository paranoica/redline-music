import stat

import numpy as np
import pytest
import soundfile as sf
from redline.flip import model, render
from redline.flip.model import Block, Effect

pytest.importorskip("redline_dsp")

RATE = 48000


def smooth_noise(frames, seed, amp=0.9):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((frames + 1200, 2))
    kernel = np.ones(600) / 600
    x = np.stack([np.convolve(x[:, c], kernel, mode="same") for c in range(2)], axis=1)[600:-600]
    return (amp * x / np.abs(x).max()).astype(np.float32)


def white(frames, seed, amp=0.5):
    rng = np.random.default_rng(seed)
    return (amp * rng.uniform(-1, 1, (frames, 2))).astype(np.float32)


def bars(count, length):
    return [i * length for i in range(count + 1)]


def blocks(*ranges):
    return [Block(f"b{i}", a, b) for i, (a, b) in enumerate(ranges)]


def largest_step(x):
    return float(np.abs(np.diff(x, axis=0)).max())


def test_play_bar_starts_follow_the_arrangement():
    starts = render.play_bar_starts([0, 10, 25, 45, 70], blocks((2, 4), (0, 1)))
    assert starts == [0, 20, 45, 55]


def test_splice_joint_has_no_bigger_step_than_the_sides():
    length = 4800
    stem = smooth_noise(3 * length, 1)
    arrangement = blocks((0, 1), (2, 3))
    out = render.splice(stem, bars(3, length), arrangement, RATE)
    assert out.shape == (2 * length, 2)
    own = max(largest_step(stem[:length]), largest_step(stem[2 * length :]))
    hard = np.abs(stem[length - 1] - stem[2 * length]).max()
    assert hard > 3 * own
    assert largest_step(out) <= own


def test_splice_contiguous_joint_is_bit_identical():
    length = 4800
    stem = white(4 * length, 5)
    out = render.splice(stem, bars(4, length), blocks((0, 2), (2, 4)), RATE)
    assert np.array_equal(out, stem)
    out = render.splice(stem, bars(4, length), blocks((0, 1), (1, 3), (0, 1)), RATE)
    assert np.array_equal(out[: 3 * length - 240], stem[: 3 * length - 240])
    assert np.array_equal(out[: 2 * length], stem[: 2 * length])


def test_splice_only_changes_samples_around_a_cut():
    length = 4800
    stem = white(4 * length, 6)
    out = render.splice(stem, bars(4, length), blocks((0, 1), (2, 3), (3, 4)), RATE)
    assert np.array_equal(out[: length - 240], stem[: length - 240])
    assert np.array_equal(out[length + 240 : 2 * length], stem[2 * length + 240 : 3 * length])
    assert not np.array_equal(out[length - 100 : length + 100], stem[length - 100 : length + 100])


def test_splice_refuses_bars_the_source_lacks():
    with pytest.raises(ValueError, match="outside"):
        render.splice(white(9600, 1), bars(2, 4800), blocks((0, 3)), RATE)


def test_reverse_reverses_the_range_and_nothing_else():
    length = 4800
    mix = white(4 * length, 7)
    out = render.apply_effects(mix, bars(4, length), [Effect("reverse", 1, 3, {})], RATE)
    fade = 480
    a, b = length, 3 * length
    flipped = mix[a:b][::-1]
    assert np.allclose(out[a + fade : b - fade], flipped[fade:-fade])
    assert abs(out[a]).max() < 0.01 and abs(out[b - 1]).max() < 0.01
    assert np.array_equal(out[:a], mix[:a])
    assert np.array_equal(out[b:], mix[b:])


def test_stutter_at_range_start_repeats_the_previous_bar():
    length = 24000
    mix = np.concatenate([white(length, s) for s in range(4)])
    starts = bars(4, length)
    stutter = Effect("stutter", 2, 3, {"length": 250, "repeats": 4})
    out = render.apply_effects(mix, starts, [stutter], RATE)
    a, b = starts[2], starts[3]
    slice_len = 12000
    previous = mix[a - slice_len : a]
    assert np.allclose(out[a + 300 : a + slice_len - 300], previous[300:-300], atol=1e-4)
    assert np.allclose(out[a + slice_len + 300 : b - 600], previous[300:-600], atol=1e-4)
    assert not np.allclose(out[a:b], mix[a:b], atol=0.05)
    assert np.array_equal(out[:a], mix[:a])
    assert np.array_equal(out[b:], mix[b:])
    assert np.allclose(out[b - 1], mix[b - 1], atol=1e-6)


def test_stutter_in_the_first_bar_has_nothing_to_repeat():
    length = 24000
    mix = white(2 * length, 3)
    out = render.apply_effects(
        mix, bars(2, length), [Effect("stutter", 0, 1, {"length": 250, "repeats": 2})], RATE
    )
    assert np.abs(out[1000:11000]).max() < 1e-4


def test_tape_stop_is_silent_after_its_duration_until_the_range_end():
    length = RATE
    mix = white(4 * length, 8)
    starts = bars(4, length)
    out = render.apply_effects(mix, starts, [Effect("tape_stop", 1, 3, {"duration": 0.25})], RATE)
    a, b = starts[1], starts[3]
    gap = int(0.25 * RATE)
    assert np.abs(out[a + gap + 480 : b - 480]).max() < 1e-6
    assert np.abs(out[a : a + gap // 2]).max() > 0.05
    assert np.array_equal(out[:a], mix[:a])
    assert np.array_equal(out[b:], mix[b:])
    assert np.allclose(out[b - 1], mix[b - 1], atol=1e-6)


def test_tape_start_is_silent_until_its_time():
    length = RATE
    mix = white(3 * length, 9)
    starts = bars(3, length)
    out = render.apply_effects(mix, starts, [Effect("tape_start", 1, 2, {"time": 0.5})], RATE)
    a = starts[1]
    assert np.abs(out[a : a + RATE // 2 - 100]).max() < 1e-6
    assert np.abs(out[a + RATE // 2 + 4800 : a + RATE // 2 + 9600]).max() > 0.01
    assert np.array_equal(out[:a], mix[:a])


def test_granular_hears_the_bars_before_its_range():
    length = RATE
    loud = white(length, 10)
    mix = np.concatenate([loud, np.zeros((length, 2), np.float32), loud])
    starts = bars(3, length)
    effect = Effect("granular", 1, 2, {"size": 80, "density": 40, "scatter": 500, "seed": 3})
    out = render.apply_effects(mix, starts, [effect], RATE)
    assert out.shape == mix.shape and np.isfinite(out).all()
    assert np.abs(out[starts[1] : starts[1] + 4800]).max() > 0.01
    again = render.apply_effects(mix, starts, [effect], RATE)
    assert np.array_equal(out, again)
    assert np.array_equal(out[: starts[1]], mix[: starts[1]])
    assert np.array_equal(out[starts[2] :], mix[starts[2] :])


def test_effect_range_outside_the_bars_is_refused():
    with pytest.raises(ValueError, match="outside"):
        render.apply_effects(white(9600, 1), bars(2, 4800), [Effect("reverse", 1, 3, {})], RATE)


def test_remove_hats_is_drums_minus_the_chosen_stems():
    drums = white(1000, 1)
    hh, ride, crash = white(1000, 2, 0.1), white(1000, 3, 0.1), white(1000, 4, 0.1)
    stems = {"hh": hh, "ride": ride, "crash": crash}
    assert np.allclose(render.remove_hats(drums, stems, ["hh"]), drums - hh)
    assert np.allclose(render.remove_hats(drums, stems, ["hh", "crash"]), drums - hh - crash)
    assert np.array_equal(render.remove_hats(drums, stems, []), drums)
    with pytest.raises(ValueError, match="shape"):
        render.remove_hats(drums, {"hh": hh[:900]}, ["hh"])
    with pytest.raises(ValueError, match="ride"):
        render.remove_hats(drums, {"hh": hh}, ["ride"])
    assert np.array_equal(drums, drums.copy())


def flip_for(**changes):
    data = {
        "format": 1,
        "source": "input/beats/x.wav",
        "grid": {"bpm": 240.0, "downbeat_s": 0.0, "swing": 50},
        "bass808": {"mode": "original", "params": {}},
        "hats": {"mode": "original", "remove": ["hh"], "patterns": ["eighths"]},
        "gains": {"drums": 0, "other": 0, "bass": 0},
        "arrangement": [{"label": "a", "from_bar": 0, "to_bar": 4}],
        "effects": [],
    }
    for key, value in changes.items():
        section, _, name = key.partition("__")
        if name:
            data[section][name] = value
        else:
            data[key] = value
    return model.parse(data)


def stems():
    n = 5 * RATE
    return {
        "drums": np.full((n, 2), 0.1, np.float32),
        "bass": np.full((n, 2), 0.2, np.float32),
        "other": np.full((n, 2), 0.4, np.float32),
        "vocals": np.full((n, 2), 0.01, np.float32),
        "hh": np.full((n, 2), 0.03, np.float32),
    }


def ours(n=4 * RATE):
    return {
        "bass808": np.full((n, 2), 0.5, np.float32),
        "hats": np.full((n - 10, 2), 0.05, np.float32),
    }


def test_mix_flip_sums_stems_with_gains():
    out = render.mix_flip(
        stems(), ours(), flip_for(gains={"drums": 6.0206, "other": 0, "bass": -6.0206})
    )
    assert out.shape == (4 * RATE, 2) and out.dtype == np.float32
    assert np.allclose(out, 0.2 + 0.41 + 0.1, atol=1e-4)


@pytest.mark.parametrize(
    "mode, expected",
    # ours comes in at the original's level
    [("original", 0.1 + 0.2 + 0.41), ("ours", 0.1 + 0.2 + 0.41), ("off", 0.1 + 0.41)],
)
def test_mix_flip_bass_modes(mode, expected):
    out = render.mix_flip(stems(), ours(), flip_for(bass808__mode=mode))
    assert np.allclose(out, expected, atol=1e-5)


@pytest.mark.parametrize(
    "mode, expected",
    [("original", 0.1), ("removed", 0.07), ("ours", 0.07 + 0.03)],
)
def test_mix_flip_hats_modes(mode, expected):
    out = render.mix_flip(stems(), ours(), flip_for(hats__mode=mode))
    assert np.allclose(out[: 4 * RATE - 20], expected + 0.2 + 0.41, atol=1e-5)


def test_mix_flip_hats_ours_pads_a_short_render_with_silence():
    out = render.mix_flip(stems(), ours(), flip_for(hats__mode="ours"))
    assert np.allclose(out[-1], 0.07 + 0.2 + 0.41, atol=1e-5)


def test_mix_flip_needs_what_its_modes_use():
    with pytest.raises(ValueError, match="bass808"):
        render.mix_flip(stems(), {}, flip_for(bass808__mode="ours"))
    short = {k: v[:RATE] for k, v in stems().items()}
    with pytest.raises(ValueError, match="outside"):
        render.mix_flip(short, ours(), flip_for())


def test_mix_flip_lays_effects():
    plain = render.mix_flip(stems(), ours(), flip_for())
    flip = flip_for(
        effects=[{"effect": "tape_stop", "from_bar": 1, "to_bar": 3, "params": {"duration": 0.5}}]
    )
    out = render.mix_flip(stems(), ours(), flip)
    assert np.abs(out[2 * RATE : 3 * RATE - 2000]).max() < 1e-6
    assert np.array_equal(out[:RATE], plain[:RATE])


def test_write_render_is_private_float_stereo_and_has_notes(tmp_path):
    mix = white(RATE // 10, 1)
    folder = tmp_path / "flips"
    wav = render.write_render(folder, 3, mix, RATE, "Hats: hh removed.")
    assert wav == folder / "flip-3.wav"
    info = sf.info(wav)
    assert (info.samplerate, info.channels, info.subtype) == (RATE, 2, "FLOAT")
    assert np.array_equal(sf.read(wav, dtype="float32")[0], mix)
    report = folder / "flip-3.report.md"
    for path in (wav, report):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = report.read_text()
    assert "artefacts" in text and "Hats: hh removed." in text
    assert "Editing a beat does not change its licence" in text
    assert "check the producer's terms before releasing." in text
    assert sorted(p.name for p in folder.iterdir()) == ["flip-3.report.md", "flip-3.wav"]


def test_write_render_is_atomic(tmp_path, monkeypatch):
    mix = white(RATE // 10, 1)
    render.write_render(tmp_path, 1, mix, RATE, "")
    before = (tmp_path / "flip-1.wav").read_bytes()

    def broken(file, *args, **kwargs):
        file.write(b"RIFF half")
        raise OSError("disk full")

    monkeypatch.setattr(render.sf, "write", broken)
    with pytest.raises(OSError):
        render.write_render(tmp_path, 1, white(RATE // 10, 2), RATE, "")
    assert (tmp_path / "flip-1.wav").read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["flip-1.report.md", "flip-1.wav"]


def test_write_render_refuses_another_format(tmp_path):
    with pytest.raises(ValueError):
        render.write_render(tmp_path, 1, white(100, 1), 44100, "")
    with pytest.raises(ValueError):
        render.write_render(tmp_path, 1, white(100, 1)[:, :1], RATE, "")
    assert list(tmp_path.iterdir()) == []


def test_clean_keeps_the_newest_and_the_kept(tmp_path):
    for n in (1, 2, 3, 4):
        render.write_render(tmp_path, n, white(100, n), RATE, "")
        (tmp_path / f"flip-{n}.json").write_text("{}")
    (tmp_path / "draft.json").write_text("{}")
    assert render.clean(tmp_path, {2}) == [1, 3]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        ["draft.json"]
        + [f"flip-{n}.json" for n in (1, 2, 3, 4)]
        + ["flip-2.wav", "flip-2.report.md", "flip-4.wav", "flip-4.report.md"]
    )
    assert render.clean(tmp_path, set()) == [2]
    assert render.clean(tmp_path / "none-yet" if False else tmp_path, set()) == []


def test_our_808_and_hats_sit_at_the_level_of_what_they_replace():
    n = 4 * RATE
    loud = {"bass808": np.full((n, 2), 0.9, np.float32), "hats": np.full((n, 2), 0.6, np.float32)}
    silent = np.zeros((5 * RATE, 2), np.float32)
    only_bass = {**stems(), "drums": silent, "other": silent, "vocals": silent, "hh": silent}
    assert np.allclose(
        render.mix_flip(only_bass, loud, flip_for(bass808__mode="ours")), 0.2, atol=1e-4
    )
    # the drums are only the hats here: removed, ours come in at the level the hats had
    only_hats = {**only_bass, "bass": silent, "drums": stems()["hh"], "hh": stems()["hh"]}
    assert np.allclose(
        render.mix_flip(only_hats, loud, flip_for(hats__mode="ours")), 0.03, atol=1e-4
    )


def test_a_mix_over_the_ceiling_is_lowered_to_it_and_says_by_how_much():
    hot = np.full((RATE, 2), 2.0, np.float32)
    lowered, gain_db = render.fit_ceiling(hot)
    assert float(np.abs(lowered).max()) <= 10 ** (render.CEILING_DB / 20) + 1e-6
    assert gain_db == pytest.approx(render.CEILING_DB - 20 * np.log10(2.0), abs=1e-3)
    calm = np.full((RATE, 2), 0.5, np.float32)
    same, none = render.fit_ceiling(calm)
    assert none == 0.0 and np.array_equal(same, calm)
