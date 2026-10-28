import json

import pytest

from redline.lyrics.parse import Artist, LyricsError, parse_text
from redline.lyrics.profile import (
    build_profiles,
    count_tracks,
    dedupe,
    dumps,
    load_profile,
    merge,
    to_profile,
)

ONE = """[Куплет]
мой синий город спит (эй)
в ночи горят огни
мой синий город спит
в ночи горят огни
[Припев]
лети лети
лети лети
"""
TWO = """[Куплет 1: Host]
холодный дождь стучит
[Куплет 2: Guest]
чужие строки тут звучат
"""


def track(text):
    return parse_text(text)


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    else:
        yield value


def test_counts_of_a_track_by_hand():
    own, _ = count_tracks([track(ONE)])
    assert own["totals"]["tracks"] == 1
    assert own["structure"] == {"verse-hook": 1}
    assert own["line_syllables/verse"] == {"6": 4}
    assert own["line_syllables/hook"] == {"4": 2}
    assert own["lines_per_section/verse"] == {"4": 1}
    assert own["adlibs"] == {"эй": 1}
    assert own["adlib_lines"] == {"yes": 1, "no": 5}
    assert own["hook_repeat"] == {"yes": 2}
    assert own["rhyme_schemes/verse"] == {"ABAB": 1}
    # stop words out: мой, в
    assert own["words"]["город"] == 2 and "мой" not in own["words"] and "в" not in own["words"]
    assert own["words_end"]["спит"] == 2


def test_a_guest_section_counts_apart_from_the_host():
    own, guests = count_tracks([track(TWO)], names=("Host",))
    assert "холодный" in own["words"] and "чужие" not in own["words"]
    assert "чужие" in guests["words"]


def test_a_near_copy_of_a_track_is_counted_once():
    near = ONE.replace("лети лети", "беги беги", 1)
    other = "[Куплет]\nсовсем другая песня здесь\n"
    kept = dedupe([track(ONE), track(near), track(other)])
    assert len(kept) == 2


def test_pooled_merge_equals_counting_all_tracks_together():
    a, b = track(ONE), track(TWO)
    together, _ = count_tracks([a, b])
    parts = [count_tracks([a])[0], count_tracks([b])[0]]
    assert merge(parts) == together


def test_merging_one_part_keeps_its_shares():
    own, _ = count_tracks([track(ONE)])
    assert to_profile(merge([own], [1.0]), "artist", "x") == to_profile(own, "artist", "x")


def test_equal_weights_give_each_part_the_same_say():
    small, _ = count_tracks([track("[Куплет]\nгород\n")])
    big, _ = count_tracks([track("[Куплет]\nрека река река река\n")])
    merged = to_profile(merge([small, big], [0.5, 0.5]), "scene", "scene")
    rates = {w["word"]: w["per_1000"] for w in merged["vocabulary"]["top"]}
    assert rates["город"] == pytest.approx(500.0)
    assert rates["река"] == pytest.approx(500.0)


def test_scene_words_need_two_artists_and_small_artists_stay_out():
    artists = {"a": Artist(("A",), "A"), "b": Artist(("B",), "A"), "c": Artist(("C",), "B")}
    base = {
        "a": [track(f"[Куплет]\nобщий свет номер {i}\n") for i in range(5)],
        "b": [track(f"[Куплет]\nобщий звон номер {i}\n") for i in range(5)],
        "c": [track("[Куплет]\nредкое слово\n")],
    }
    profiles = build_profiles(base, artists, mine=[track(ONE)], min_tracks=5)
    scene_words = {w["word"] for w in profiles["scene"]["vocabulary"]["top"]}
    assert "общий" in scene_words
    assert "свет" not in scene_words
    assert "редкое" not in scene_words
    assert "свет" in {w["word"] for w in profiles["artist-a"]["vocabulary"]["top"]}
    assert profiles["scene"]["meta"]["artists"] == 2
    assert set(profiles) == {
        "artist-a",
        "artist-b",
        "artist-c",
        "group-A",
        "scene",
        "mine",
        "working",
    }
    working_words = {w["word"] for w in profiles["working"]["vocabulary"]["top"]}
    assert "город" in working_words


def test_no_profile_string_holds_a_space():
    artists = {"a": Artist(("A",), "A")}
    profiles = build_profiles({"a": [track(ONE)] * 1}, artists, mine=[track(TWO)], min_tracks=1)
    for profile in profiles.values():
        for item in walk(profile):
            assert not (isinstance(item, str) and " " in item), item


def test_profiles_are_byte_stable_and_load_back(tmp_path):
    own, _ = count_tracks([track(ONE), track(TWO)])
    profile = to_profile(own, "artist", "x")
    text = dumps(profile)
    assert text == dumps(to_profile(own, "artist", "x"))
    path = tmp_path / "x.json"
    path.write_text(text, encoding="utf-8")
    assert dumps(load_profile(path)) == text


@pytest.mark.parametrize("change", ["drop", "extra"])
def test_loading_refuses_a_missing_or_unknown_key_naming_it(tmp_path, change):
    own, _ = count_tracks([track(ONE)])
    profile = json.loads(dumps(to_profile(own, "artist", "x")))
    if change == "drop":
        del profile["rhyme"]
        expected = "rhyme"
    else:
        profile["mood"] = {}
        expected = "mood"
    path = tmp_path / "x.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(LyricsError, match=expected):
        load_profile(path)
