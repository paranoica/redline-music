import json

import pytest
from redline.cli import main
from redline.lyrics.base.fingerprint import FingerprintIndex
from redline.lyrics.base.profile import load_profile
from redline.lyrics.text.syllables import count_syllables
from redline.lyrics.write.recipe import recipe_from_json
from redline.song import text as text_module
from redline.song.text import save_hand_edit

SENTINEL = "сторожевая строка которую нельзя никому показывать вообще"
HEADERS = {
    "intro": "Интро",
    "hook": "Припев",
    "prehook": "Предприпев",
    "verse": "Куплет",
    "bridge": "Бридж",
    "outro": "Аутро",
    "other": "Скит",
}
RHYMES = [("дом", "лом"), ("сыр", "мир"), ("дуб", "суп"), ("кот", "рот")]
NEW_LINE = "та та та та та та та новая строчка тут"


def write_track(path, artist, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nartist: "{artist}"\ntrack: "{path.stem}"\n---\n{body}', encoding="utf-8")


def line_of(syllables, end):
    filler = max(0, syllables - count_syllables(end))
    return " ".join(["та"] * filler + [end])


def title_for(plan):
    pool = (
        ["ice", "age", "cold", "flow"] if plan.script == "latin" else ["лёд", "ночь", "дым", "свет"]
    )
    words = pool[: plan.words]
    if plan.digits:
        words[0] += "9"
    title = " ".join(words)
    return title.upper() if plan.case == "caps" else title


def good_draft(recipe):
    out = [f"Title: {title_for(recipe.title)}"] if recipe.title else []
    for plan in recipe.sections:
        out.append(f"[{HEADERS[plan.kind]}]")
        target = sum(plan.syllables) // 2
        for i in range(plan.lines):
            pair = RHYMES[(i // 4) % len(RHYMES)]
            if plan.scheme in ("AABB", "AAAA"):
                end = pair[(i % 4) // 2] if plan.scheme == "AABB" else pair[i % 2]
            elif plan.scheme == "ABAB":
                end = (pair[0], RHYMES[-1][0], pair[1], RHYMES[-1][1])[i % 4]
            else:
                end = (pair[0], RHYMES[-1][0], RHYMES[-1][1], pair[1])[i % 4]
            out.append(line_of(target, end))
    return "\n".join(out) + "\n"


@pytest.fixture
def seed(tmp_path):
    root = tmp_path / "lyrics"
    others = root / "others"
    others.mkdir(parents=True)
    (others / "artists.yaml").write_text(
        "fav: {name: Fav, profile: A}\nb: {name: Beta, profile: A}\n", encoding="utf-8"
    )
    for i in range(5):
        body = f"[Куплет]\n{SENTINEL} {i}\nхолодный ветер дует в спину {i}\n[Припев]\nгори гори\n"
        write_track(others / "fav" / f"t{i}.txt", "Fav", body)
        write_track(others / "b" / f"t{i}.txt", "Beta", f"[Куплет]\nсвет и тень {i}\nи ночь\n")
    own = (
        "[Verse]\nмой город спит и я не сплю опять\nкэш в кармане и некуда бежать\n"
        "огни горят и улица молчит\nи только сердце громко так стучит\n"
        "[Hook]\nэй эй ночь (ночь)\nэй эй ночь (ночь)\n"
    )
    write_track(root / "mine" / "one.txt", "me", own)
    write_track(root / "mine" / "two.txt", "me", own.replace("город", "район"))
    (root / "mix.yaml").write_text("artist-fav: 0.5\nmine: 0.25\nscene: 0.25\n", encoding="utf-8")
    build = tmp_path / "build"
    assert main(["lyrics", "index", "--lyrics", str(root), "--build", str(build)]) == 0
    args = ["lyrics", "write", "--seed", "7", "--lyrics", str(root), "--build", str(build)]
    main(args)
    folder = root / "generated" / "7"
    recipe = recipe_from_json((folder / "recipe.json").read_text(encoding="utf-8"))
    (folder / "draft-1.txt").write_text(good_draft(recipe), encoding="utf-8")
    assert main(args) == 0
    base = (folder / "text.txt").read_text(encoding="utf-8")
    name = json.loads((folder / "state.json").read_text(encoding="utf-8"))["profile"]
    return {
        "folder": folder,
        "base": base,
        "index": FingerprintIndex.load(build),
        "profile": load_profile(build / "profiles" / f"{name}.json"),
        "lyrics": root,
        "build": build,
    }


def save(seed, text, version=1, **over):
    args = {"index": seed["index"], "profile": seed["profile"], **over}
    return save_hand_edit(seed["folder"], version, text, **args)


def changed(seed, line):
    lines = seed["base"].splitlines()
    lines[1] = line
    return "\n".join(lines) + "\n"


def kinds(result):
    return [e.kind for e in result.errors]


def files(seed):
    return sorted(p.name for p in seed["folder"].glob("text*.txt"))


def test_a_good_edit_is_the_next_text_file_readable_by_the_owner_only(seed):
    text = changed(seed, NEW_LINE)
    result = save(seed, text)
    assert result.saved and result.errors == () and result.version == 2
    path = seed["folder"] / "text-2.txt"
    assert result.path == path
    assert path.read_text(encoding="utf-8") == text
    assert path.stat().st_mode & 0o777 == 0o600
    assert (seed["folder"] / "text.txt").read_text(encoding="utf-8") == seed["base"]


def test_a_second_edit_takes_the_next_free_number_and_may_start_from_any_version(seed):
    assert save(seed, changed(seed, NEW_LINE)).version == 2
    result = save(seed, changed(seed, NEW_LINE.replace("новая", "другая")), version=1)
    assert result.version == 3
    assert files(seed) == ["text-2.txt", "text-3.txt", "text.txt"]


def test_a_pending_agent_edit_refuses_and_writes_nothing(seed):
    pending = {"version": 2, "source": 1, "change": "x", "round": 1, "reasons": []}
    (seed["folder"] / "edits.json").write_text(json.dumps({"done": [], "pending": pending}))
    result = save(seed, changed(seed, NEW_LINE))
    assert kinds(result) == ["pending"] and not result.saved
    assert files(seed) == ["text.txt"]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("просто строчки без заголовка\nещё одна\n", "not lyrics"),
        ("```\n[Куплет]\nтекст\n```\n", "not lyrics"),
        ("[Куплет]\n" + "та " * 1100 + "\n", "length"),
    ],
    ids=["no header", "fence", "long"],
)
def test_the_editors_checks_refuse_a_text_and_nothing_is_written(seed, text, kind):
    result = save(seed, text)
    assert kinds(result) == [kind]
    assert result.errors[0].place and result.errors[0].message
    assert files(seed) == ["text.txt"]


def test_too_many_ordinary_places_are_refused(seed):
    lines = seed["base"].splitlines()
    for i, place in enumerate(("остановка", "поликлиника", "больница"), start=1):
        lines[i] = line_of(count_syllables(lines[i]), place)
    result = save(seed, "\n".join(lines) + "\n")
    assert "places" in kinds(result)
    assert files(seed) == ["text.txt"]


def test_a_stretch_of_another_artists_text_is_refused_naming_its_lines(seed):
    result = save(seed, changed(seed, f"{SENTINEL} 0"))
    assert kinds(result) == ["copy"]
    assert result.errors[0].place == "lines 1-1"
    assert files(seed) == ["text.txt"]


def test_the_copy_check_uses_the_threshold_the_seed_was_written_with(seed):
    line = " ".join(SENTINEL.split()[:6]) + " та"
    state = seed["folder"] / "state.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data["min_words"] == 8
    assert save(seed, changed(seed, line)).saved
    (seed["folder"] / "text-2.txt").unlink()
    data["min_words"] = 5
    state.write_text(json.dumps(data), encoding="utf-8")
    assert kinds(save(seed, changed(seed, line))) == ["copy"]


@pytest.mark.parametrize("missing", ["index", "profile"])
def test_a_missing_index_or_profile_refuses_with_the_command_that_builds_it(seed, missing):
    over = {missing: None}
    result = save(seed, changed(seed, NEW_LINE), lyrics=seed["lyrics"], build=seed["build"], **over)
    assert kinds(result) == [f"no {missing}"]
    message = result.errors[0].message
    assert f"redline lyrics index --lyrics {seed['lyrics']} --build {seed['build']}" in message
    assert files(seed) == ["text.txt"]


def test_an_unknown_base_and_an_unsaved_seed_are_refused(seed, tmp_path):
    assert kinds(save(seed, changed(seed, NEW_LINE), version=9)) == ["base"]
    empty = tmp_path / "empty"
    empty.mkdir()
    result = save_hand_edit(empty, 1, "x", index=seed["index"], profile=seed["profile"])
    assert kinds(result) == ["damaged"]
    assert list(empty.iterdir()) == []


def test_a_file_that_appeared_meanwhile_is_never_overwritten(seed, monkeypatch):
    taken = seed["folder"] / "text-2.txt"
    taken.write_text("чужой файл", encoding="utf-8")
    monkeypatch.setattr(text_module, "versions", lambda folder: {1: folder / "text.txt"})
    result = save(seed, changed(seed, NEW_LINE))
    assert kinds(result) == ["exists"] and result.errors[0].place == "text-2.txt"
    assert taken.read_text(encoding="utf-8") == "чужой файл"


def test_a_title_line_goes_to_its_own_file(seed):
    result = save(seed, "Title: ночной дым\n" + changed(seed, NEW_LINE))
    assert result.saved
    assert (seed["folder"] / "title-2.txt").read_text(encoding="utf-8") == "ночной дым\n"
    assert "Title" not in result.path.read_text(encoding="utf-8")
