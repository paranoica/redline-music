import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

redline_dsp = pytest.importorskip("redline_dsp")

from redline.beat import handoff  # noqa: E402
from redline.beat.checks import check_score  # noqa: E402
from redline.beat.recipe import (  # noqa: E402
    GENRES,
    RecipeError,
    make_recipe,
    recipe_from_json,
    recipe_json,
)
from redline.cli import main  # noqa: E402

KICK = "x.....x........."
SNARE = "........x......."
HAT = "x.x.x.x.x.x.x.x."
# the open hat only in the last bar of 8, so which sounds play changes every 8 bars
OPEN_LAST = "." * 16 * 7 + ".............x.."
BASS = [
    {"step": 2, "len": 6, "note": "A1", "vel": 0.9},
    {"step": 10, "len": 4, "note": "E2", "vel": 0.8},
]
BELL = [
    {"step": 0, "len": 4, "note": "A4", "vel": 0.7},
    {"step": 4, "len": 4, "note": "C5", "vel": 0.7},
    {"step": 8, "len": 8, "note": "E5", "vel": 0.7},
]


def trap(**changes):
    recipe = make_recipe(11, genre="trap", bpm=140, key="A minor")
    return replace(recipe, **{"bars": (16, 48), **changes})


def score(bars=(8, 16), **top):
    """A score the trap recipe takes with no error and no warning."""
    body = {
        "version": 1,
        "bpm": 140,
        "swing": 52,
        "seed": 11,
        "key": "A minor",
        "sounds": {
            "kick": {"instrument": "kick"},
            "bass": {"instrument": "bass808"},
            "snare": {"instrument": "snare"},
            "hat": {"instrument": "hat_closed"},
            "open_hat": {"instrument": "hat_open"},
            "bell": {"instrument": "fm", "params": {"preset": "bell"}},
        },
        "patterns": {
            "groove": {
                "bars": 8,
                "rows": {
                    "kick": KICK * 8,
                    "snare": SNARE * 8,
                    "hat": HAT * 8,
                    "open_hat": OPEN_LAST,
                },
            },
            "low": {"bars": 1, "rows": {"bass": BASS}},
            "melody": {"bars": 1, "rows": {"bell": BELL}},
        },
        "sections": [
            {"name": f"part{i}", "bars": n, "play": ["groove", "low", "melody"]}
            for i, n in enumerate(bars)
        ],
    }
    body.update(top)
    # the row lists are shared by every call; tests change their copies
    return json.loads(json.dumps(body))


def text(body):
    return json.dumps(body)


def errors_of(body, recipe=None):
    return check_score(text(body), recipe or trap()).errors


def warnings_of(body, recipe=None):
    return check_score(text(body), recipe or trap()).warnings


# recipe


def test_the_same_seed_and_options_draw_the_same_recipe():
    assert make_recipe(7) == make_recipe(7)
    assert make_recipe(7, genre="phonk", bpm=133, key="F# minor") == make_recipe(
        7, genre="phonk", bpm=133, key="F# minor"
    )


def test_seeds_draw_different_recipes():
    drawn = {recipe_json(make_recipe(seed)) for seed in range(12)}
    assert len(drawn) == 12
    assert len({make_recipe(seed).genre for seed in range(60)}) == len(GENRES)


def test_a_recipe_comes_back_from_its_json():
    for seed in range(5):
        recipe = make_recipe(seed)
        assert recipe_from_json(recipe_json(recipe)) == recipe


def test_options_override_only_what_they_name():
    drawn = make_recipe(21)
    by_bpm = make_recipe(21, bpm=123)
    assert by_bpm.bpm == 123
    assert replace(by_bpm, bpm=drawn.bpm) == drawn
    by_key = make_recipe(21, key="Bb dorian")
    assert by_key.key == "Bb dorian"
    assert replace(by_key, key=drawn.key) == drawn
    assert make_recipe(21, genre="lofi").genre == "lofi"


@pytest.mark.parametrize("genre", sorted(GENRES))
def test_every_genre_draws_its_bpm_swing_and_modes_inside_its_ranges(genre):
    spec = GENRES[genre]
    for seed in range(40):
        recipe = make_recipe(seed, genre=genre)
        low, high = spec.bpm
        assert low <= recipe.bpm <= high
        assert spec.swing[0] <= recipe.swing <= spec.swing[1]
        assert recipe.key.split(" ")[1] in spec.modes
        assert 2 <= len(recipe.devices) <= 3
        assert len(set(recipe.devices)) == len(recipe.devices)
        total = sum(part.bars for part in recipe.structure)
        assert recipe.bars[0] <= total <= recipe.bars[1]
    assert spec.source in ("blog, unverified", "own choice")


def test_every_genre_has_its_sounds_and_structures():
    for name, spec in GENRES.items():
        assert spec.sounds, name
        assert spec.structures, name
        assert {"kick", "bass808"} <= {s.instrument for s in spec.sounds}, name


@pytest.mark.parametrize(
    "options",
    [{"genre": "rage"}, {"bpm": 300}, {"bpm": 10}, {"key": "H minor"}, {"key": "C blues"}],
)
def test_options_outside_the_known_are_refused(options):
    with pytest.raises(RecipeError):
        make_recipe(1, **options)


def test_a_negative_seed_is_refused():
    with pytest.raises(RecipeError):
        make_recipe(-1)


# checks: errors


def test_a_valid_score_has_no_errors_and_no_warnings():
    checked = check_score(text(score()), trap())
    assert checked.errors == ()
    assert checked.warnings == ()


def test_a_rust_error_comes_through_with_its_path():
    body = score()
    body["patterns"]["groove"]["rows"]["hat"] = HAT[:-1] * 8
    errors = errors_of(body)
    assert any(e.startswith("patterns.groove.rows.hat: ") for e in errors)


def test_a_note_out_of_the_scale_is_an_error_with_its_path():
    body = score()
    body["patterns"]["melody"]["rows"]["bell"][1]["note"] = "C#5"
    errors = errors_of(body)
    assert len(errors) == 1
    assert errors[0].startswith("patterns.melody.rows.bell[1].note: ")


def test_a_passing_note_is_allowed_out_of_the_scale():
    body = score()
    body["patterns"]["melody"]["rows"]["bell"][1].update(note="C#5", passing=True)
    assert errors_of(body) == ()


def test_the_scale_is_the_recipes_key_spelled_either_way():
    body = score(key="Gb minor")
    recipe = trap(key="F# minor")
    body["patterns"]["melody"]["rows"]["bell"] = [
        {"step": 0, "len": 4, "note": "F#4", "vel": 0.7},
        {"step": 4, "len": 4, "note": "C#5", "vel": 0.7},
    ]
    body["patterns"]["low"]["rows"]["bass"] = [{"step": 2, "len": 6, "note": "F#1", "vel": 0.9}]
    assert errors_of(body, recipe) == ()


def test_a_key_other_than_the_recipes_is_an_error():
    errors = errors_of(score(key="E minor"))
    assert any(e.startswith("key: ") for e in errors)


@pytest.mark.parametrize(
    ("note", "bad"), [("A0", True), ("B0", True), ("C1", False), ("G3", False), ("A3", True)]
)
def test_808_notes_keep_inside_c1_to_g3(note, bad):
    body = score()
    body["patterns"]["low"]["rows"]["bass"][0]["note"] = note
    errors = errors_of(body)
    assert bool(errors) == bad
    if bad:
        assert errors[0].startswith("patterns.low.rows.bass[0].note: ")


def test_the_808_range_counts_its_tune():
    body = score()
    body["sounds"]["bass"]["params"] = {"tune": -12}
    body["patterns"]["low"]["rows"]["bass"][0]["note"] = "C1"
    assert any("bass[0].note" in e for e in errors_of(body))


def test_other_note_sounds_are_not_held_to_the_808_range():
    body = score()
    body["patterns"]["melody"]["rows"]["bell"][0]["note"] = "A5"
    assert errors_of(body) == ()


@pytest.mark.parametrize(
    ("bars", "bad"), [((8,), True), ((8, 8), False), ((8, 8, 8, 8, 8, 8, 8), True)]
)
def test_total_bars_keep_inside_the_recipes_range(bars, bad):
    errors = errors_of(score(bars=bars))
    assert any(e.startswith("sections: ") for e in errors) == bad


@pytest.mark.parametrize(("bars", "bad"), [((200.0,), True), ((8.0,), True), ((8.0, 8), False)])
def test_whole_bar_counts_written_as_floats_are_held_to_the_range(bars, bad):
    errors = errors_of(score(bars=bars))
    assert any(e.startswith("sections: ") for e in errors) == bad


def test_a_bar_count_with_a_fraction_is_not_counted():
    errors = errors_of(score(bars=(200.5,)))
    assert not any(e.startswith("sections: ") for e in errors)


@pytest.mark.parametrize(("bpm", "bad"), [(140.4, False), (139.6, False), (141, True), (120, True)])
def test_the_bpm_is_the_recipes(bpm, bad):
    errors = errors_of(score(bpm=bpm))
    assert any(e.startswith("bpm: ") for e in errors) == bad


def test_a_sampler_library_must_be_in_the_manifest():
    body = score()
    body["sounds"]["keys"] = {"instrument": "sampler", "library": "no-such-library"}
    errors = errors_of(body)
    assert errors == ("sounds.keys.library: no-such-library is not in the sound library",)
    body["sounds"]["keys"]["library"] = "vcsl-glock"
    assert errors_of(body) == ()


def test_text_that_is_not_json_is_an_error_not_an_exception():
    checked = check_score("{", trap())
    assert len(checked.errors) == 1
    assert checked.warnings == ()


# checks: warnings


def kinds(warnings):
    return {w.kind for w in warnings}


def test_too_few_hats_a_bar_is_a_warning_with_its_source():
    body = score()
    body["patterns"]["groove"]["rows"]["hat"] = "x..............." * 8
    warned = [w for w in warnings_of(body) if w.kind == "density"]
    assert warned and all(w.source == "own choice" for w in warned)
    assert all("hat" in w.message for w in warned)
    assert errors_of(body) == ()


def test_too_many_kicks_a_bar_is_a_warning():
    body = score()
    body["patterns"]["groove"]["rows"]["kick"] = "x.x.x.x.x.x.x.x." * 8
    body["patterns"]["low"]["rows"]["bass"] = [{"step": 1, "len": 6, "note": "A1", "vel": 0.9}]
    warned = [w for w in warnings_of(body) if w.kind == "density"]
    assert warned and all("kick" in w.message for w in warned)


def test_the_same_bar_more_than_8_times_is_a_warning():
    body = score(bars=(16,))
    body["patterns"]["groove"]["rows"]["open_hat"] = "." * 128
    warned = warnings_of(body)
    assert "repeat" in kinds(warned)
    assert all(w.source in ("blog, unverified", "own choice") for w in warned)


def test_the_same_bar_8_times_is_not_a_warning():
    body = score(bars=(8, 8))
    body["patterns"]["groove"]["rows"]["open_hat"] = "." * 128
    body["sections"][1]["mute"] = ["bell"]
    assert warnings_of(body) == ()


def test_a_kick_on_an_808_note_start_is_a_warning():
    body = score()
    body["patterns"]["groove"]["rows"]["kick"] = "x.x...x........." * 8
    warned = [w for w in warnings_of(body) if w.kind == "clash"]
    assert len(warned) == 1
    assert warned[0].source == "blog, unverified"


@pytest.mark.parametrize(("bass_swing", "clash"), [(52, True), (55, False)])
def test_a_clash_is_a_kick_within_10_ms_of_the_808(bass_swing, clash):
    # at 140 BPM the 808's own swing moves its odd-step note 4.3 ms (52) or 10.7 ms (55) late
    body = score()
    body["sounds"]["kick"]["swing"] = 50
    body["sounds"]["bass"]["swing"] = bass_swing
    body["patterns"]["groove"]["rows"]["kick"] = "x..x..x........." * 8
    body["patterns"]["low"]["rows"]["bass"] = [{"step": 3, "len": 4, "note": "A1", "vel": 0.9}]
    assert ("clash" in kinds(warnings_of(body))) == clash


def test_a_kick_well_off_an_808_note_is_no_clash():
    body = score()
    body["patterns"]["groove"]["rows"]["kick"] = "x..x..x........." * 8
    assert "clash" not in kinds(warnings_of(body))


def test_more_than_8_bars_with_the_same_sounds_is_a_warning():
    body = score(bars=(16,))
    body["patterns"]["groove"]["rows"]["open_hat"] = "." * 128
    # every bar differs from the one before, the sounds that play do not
    body["patterns"]["groove"]["rows"]["hat"] = (HAT + "xxxxxxxxxxxxxxxx") * 4
    warned = warnings_of(body)
    assert "repeat" not in kinds(warned)
    assert "unchanged" in kinds(warned)


def test_warnings_never_refuse_a_score():
    body = score(bars=(16,))
    body["patterns"]["groove"]["rows"]["open_hat"] = "." * 128
    body["patterns"]["groove"]["rows"]["kick"] = "x.x...x........." * 8
    checked = check_score(text(body), trap())
    assert checked.warnings and checked.errors == ()


# the prompt


def test_the_example_in_the_prompt_passes_the_score_check():
    prompt = handoff.PROMPT.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", prompt, re.S)
    assert blocks
    for block in blocks:
        assert redline_dsp.beat_check(block) == []


def test_the_prompt_lists_every_live_effect_and_no_offline_one():
    effects = json.loads(redline_dsp.rack_effects())
    prompt = handoff.PROMPT.read_text(encoding="utf-8")
    listed = re.search(r"Effects you may use: (.*?)\.\n", prompt, re.S)
    assert listed
    names = {n.strip() for n in listed.group(1).replace("\n", " ").split(",")}
    assert names == {e["effect"] for e in effects if not e["offline_only"]}


def test_the_sampler_list_changes_only_the_sampler_choice(monkeypatch):
    from redline.beat import recipe as recipe_module

    def drawn(samplers):
        monkeypatch.setattr(recipe_module, "_samplers", lambda: samplers)
        return [make_recipe(seed) for seed in range(40)]

    def without_library(r):
        return replace(r, sounds=tuple(replace(s, library=None) for s in r.sounds))

    none, some = drawn(()), drawn(("lib_a", "lib_b", "lib_c"))
    assert [without_library(r) for r in none] == [without_library(r) for r in some]
    # the choice itself is still drawn when there is something to choose
    assert any(s.library for r in some for s in r.sounds)


# handoff


def answer(pending, body):
    pending.draft.write_text(body if isinstance(body, str) else text(body), encoding="utf-8")


def start(root, seed=5):
    return handoff.write_beat(root, seed, genre="trap", bpm=140, key="A minor")


def fitting(root, seed=5):
    """A valid score for the seed's own recipe, whatever bars it drew."""
    recipe = recipe_from_json((root / str(seed) / "recipe.json").read_text(encoding="utf-8"))
    low = recipe.bars[0]
    sections = (low + 7) // 8
    assert 8 * sections <= recipe.bars[1]
    return score(bars=(8,) * sections, seed=seed)


def test_the_first_run_puts_out_a_request_and_waits(tmp_path):
    result = start(tmp_path)
    assert isinstance(result, handoff.Pending)
    assert result.request == tmp_path / "5" / "request-1.md"
    assert result.draft == tmp_path / "5" / "score-draft-1.json"
    request = result.request.read_text(encoding="utf-8")
    assert '"genre": "trap"' in request
    assert "bass808" in request
    assert str(result.draft) in request
    # asked again before the agent answers, nothing changes
    again = start(tmp_path)
    assert again == result


def test_a_valid_draft_on_round_1_is_saved_as_score_1(tmp_path):
    pending = start(tmp_path)
    answer(pending, fitting(tmp_path))
    result = start(tmp_path)
    assert isinstance(result, handoff.Saved)
    assert result.path == tmp_path / "5" / "score-1.json"
    assert json.loads(result.path.read_text(encoding="utf-8")) == fitting(tmp_path)
    state = json.loads((tmp_path / "5" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "saved"
    assert start(tmp_path) == result


def test_an_invalid_draft_comes_back_with_its_problems_then_a_valid_one_is_saved(tmp_path):
    pending = start(tmp_path)
    bad = fitting(tmp_path)
    bad["patterns"]["melody"]["rows"]["bell"][0]["note"] = "A#4"
    bad["patterns"]["groove"]["rows"]["kick"] = "x.x...x........." * 8
    answer(pending, bad)
    second = start(tmp_path)
    assert isinstance(second, handoff.Pending)
    assert second.request.name == "request-2.md"
    request = second.request.read_text(encoding="utf-8")
    assert "patterns.melody.rows.bell[0].note" in request
    assert "kick" in request and "blog, unverified" in request
    # the last draft is quoted, so the agent can fix it rather than start over
    assert '"A#4"' in request
    answer(second, fitting(tmp_path))
    saved = start(tmp_path)
    assert isinstance(saved, handoff.Saved)
    assert saved.path.name == "score-1.json"


def test_four_invalid_rounds_fail(tmp_path):
    result = start(tmp_path)
    for _ in range(4):
        assert isinstance(result, handoff.Pending)
        answer(result, score(bpm=99))
        result = start(tmp_path)
    assert isinstance(result, handoff.Failed)
    assert result.status == "failed"
    assert any(r.startswith("bpm: ") for r in result.reasons)
    assert not (tmp_path / "5" / "request-5.md").exists()
    assert start(tmp_path) == result


def test_a_refusal_stops_the_seed(tmp_path):
    pending = start(tmp_path)
    answer(pending, "REFUSED: the recipe asks for a sound that does not exist\n")
    result = start(tmp_path)
    assert result == handoff.Failed("refused", ("the recipe asks for a sound that does not exist",))
    assert start(tmp_path) == result


def test_other_options_for_a_started_seed_are_refused_unless_forced(tmp_path):
    start(tmp_path)
    with pytest.raises(handoff.BeatError):
        handoff.write_beat(tmp_path, 5, genre="lofi")
    forced = handoff.write_beat(tmp_path, 5, genre="lofi", force=True)
    assert isinstance(forced, handoff.Pending)
    assert '"genre": "lofi"' in forced.request.read_text(encoding="utf-8")


def test_a_forced_saved_seed_saves_the_next_free_score(tmp_path):
    answer(start(tmp_path), fitting(tmp_path))
    start(tmp_path)
    pending = handoff.write_beat(tmp_path, 5, genre="trap", bpm=140, key="A minor", force=True)
    assert (tmp_path / "5" / "score-1.json").exists()
    answer(pending, fitting(tmp_path))
    saved = start(tmp_path)
    assert isinstance(saved, handoff.Saved)
    assert saved.path.name == "score-2.json"


def mark(genre, verdict, excerpt, note=""):
    return json.dumps(
        {
            "seed": 1,
            "bar": 3,
            "verdict": verdict,
            "note": note,
            "excerpt": excerpt,
            "genre": genre,
        }
    )


def test_a_request_quotes_marks_of_the_same_genre_only(tmp_path):
    lines = [mark("trap", "bad", f"trap-bad-{i}", "too busy") for i in range(6)]
    lines += [mark("trap", "good", f"trap-good-{i}") for i in range(2)]
    lines += [mark("lofi", "bad", "lofi-bad"), mark("lofi", "good", "lofi-good")]
    lines += ["not json", json.dumps({"genre": "trap", "verdict": "bad"})]
    (tmp_path / "marks.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    request = start(tmp_path).request.read_text(encoding="utf-8")
    assert "lofi-" not in request
    quoted_bad = re.findall(r"trap-bad-\d", request)
    assert quoted_bad == [f"trap-bad-{i}" for i in (2, 3, 4, 5)]
    assert re.findall(r"trap-good-\d", request) == ["trap-good-0", "trap-good-1"]
    assert "too busy" in request


def test_a_request_without_marks_has_no_marks_section(tmp_path):
    request = start(tmp_path).request.read_text(encoding="utf-8")
    request = request.replace(str(tmp_path), "")
    assert "marks" not in request.lower().split("## recipe")[1]


# CLI


def test_the_cli_round_trip(tmp_path, capsys):
    argv = ["beat", "write", "--seed", "5", "--genre", "trap", "--bpm", "140"]
    argv += ["--key", "A minor", "--root", str(tmp_path)]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "5" / "request-1.md") in out
    assert "waiting" in out
    (tmp_path / "5" / "score-draft-1.json").write_text(text(fitting(tmp_path)), encoding="utf-8")
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert str(tmp_path / "5" / "score-1.json") in out
    assert Path(tmp_path / "5" / "score-1.json").exists()


def test_the_cli_reports_a_failed_seed_with_exit_1(tmp_path, capsys):
    argv = ["beat", "write", "--seed", "6", "--root", str(tmp_path)]
    assert main(argv) == 0
    (tmp_path / "6" / "score-draft-1.json").write_text(
        "REFUSED: cannot do this one", encoding="utf-8"
    )
    capsys.readouterr()
    assert main(argv) == 1
    assert "cannot do this one" in capsys.readouterr().out


def test_the_cli_refuses_a_bad_option_with_exit_2(tmp_path, capsys):
    argv = ["beat", "write", "--seed", "6", "--key", "H minor", "--root", str(tmp_path)]
    assert main(argv) == 2
    assert "H minor" in capsys.readouterr().err


def test_a_saved_draft_never_replaces_a_score_written_meanwhile(tmp_path, monkeypatch):
    answer(start(tmp_path), fitting(tmp_path))
    folder = tmp_path / "5"
    real = handoff._next_score

    # the studio saves score-1.json between the number being picked and the write
    def racing(where):
        found = real(where)
        found.write_text("{}", encoding="utf-8")
        return found

    monkeypatch.setattr(handoff, "_next_score", racing)
    saved = start(tmp_path)
    assert isinstance(saved, handoff.Saved)
    assert saved.path == folder / "score-2.json"
    assert (folder / "score-1.json").read_text(encoding="utf-8") == "{}"
    assert json.loads(saved.path.read_text(encoding="utf-8")) == fitting(tmp_path)
    assert saved.path.stat().st_mode & 0o777 == 0o600
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    assert state["score"] == "score-2.json"
    assert [p.name for p in folder.iterdir() if p.name.endswith(".tmp")] == []
