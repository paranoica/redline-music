import json
import subprocess

import pytest

from redline.cli import main
from redline.lyrics.fingerprint import FingerprintIndex
from redline.lyrics.parse import parse_text
from redline.lyrics.recipe import recipe_from_json
from redline.lyrics.syllables import count_syllables

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


def write_track(path, artist, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nartist: "{artist}"\ntrack: "{path.stem}"\n---\n{body}', encoding="utf-8")


@pytest.fixture
def base(tmp_path):
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
    return root, build


def run(base, seed, *extra):
    root, build = base
    args = ["lyrics", "write", "--seed", str(seed), "--lyrics", str(root), "--build", str(build)]
    return main([*args, *extra])


def folder(base, seed):
    return base[0] / "generated" / str(seed)


def recipe_of(base, seed):
    return recipe_from_json((folder(base, seed) / "recipe.json").read_text(encoding="utf-8"))


def line_of(syllables, end):
    filler = max(0, syllables - count_syllables(end))
    return " ".join(["та"] * filler + [end])


def good_draft(recipe):
    out = []
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


def draft(base, seed, text, round_=1):
    (folder(base, seed) / f"draft-{round_}.txt").write_text(text, encoding="utf-8")


def test_a_new_seed_freezes_the_recipe_and_waits_for_the_writer(base, capsys):
    assert run(base, 7) == 3
    out = capsys.readouterr().out
    assert "request-1.md" in out
    seed_folder = folder(base, 7)
    first = (seed_folder / "recipe.json").read_bytes()
    state = json.loads((seed_folder / "state.json").read_text(encoding="utf-8"))
    assert (state["status"], state["round"]) == ("pending", 1)
    assert run(base, 7) == 3
    assert (seed_folder / "recipe.json").read_bytes() == first


def test_nothing_of_another_artist_reaches_the_writer(base):
    run(base, 3)
    for path in folder(base, 3).iterdir():
        text = path.read_text(encoding="utf-8")
        assert "сторожевая строка" not in text, path.name
        assert "fav" not in text.lower(), path.name


def test_the_request_carries_the_owners_excerpts_only(base):
    run(base, 3)
    request = (folder(base, 3) / "request-1.md").read_text(encoding="utf-8")
    assert "кэш в кармане" in request or "огни горят" in request or "эй эй ночь" in request


def test_a_good_draft_is_saved_and_printed_again(base, capsys):
    run(base, 5)
    draft(base, 5, good_draft(recipe_of(base, 5)))
    assert run(base, 5) == 0
    saved = (folder(base, 5) / "text.txt").read_text(encoding="utf-8")
    checks = json.loads((folder(base, 5) / "checks.json").read_text(encoding="utf-8"))
    assert checks["copies"] == 0
    capsys.readouterr()
    assert run(base, 5) == 0
    assert saved.splitlines()[0] in capsys.readouterr().out


def test_a_copied_draft_goes_back_naming_its_lines_only(base):
    run(base, 4)
    copied = good_draft(recipe_of(base, 4)).replace(
        "\n", "\nмой город спит и я не сплю опять кэш в кармане\n", 1
    )
    draft(base, 4, copied)
    assert run(base, 4) == 3
    request = (folder(base, 4) / "request-2.md").read_text(encoding="utf-8")
    assert "copy" in request and "мой город спит" in request
    assert "сторожевая строка" not in request


def test_a_draft_copying_a_saved_text_is_refused(base):
    run(base, 1)
    text = good_draft(recipe_of(base, 1))
    text = text.replace("\n", "\nсовсем новая строка про ветер и холодный снег зимой\n", 1)
    draft(base, 1, text)
    run(base, 1)
    if not (folder(base, 1) / "text.txt").exists():
        pytest.skip("the seed's own shape refused the planted line")
    run(base, 2)
    other = good_draft(recipe_of(base, 2)).replace(
        "\n", "\nсовсем новая строка про ветер и холодный снег зимой\n", 1
    )
    draft(base, 2, other)
    assert run(base, 2) == 3


def test_a_missing_draft_keeps_waiting(base, capsys):
    run(base, 6)
    capsys.readouterr()
    assert run(base, 6) == 3
    assert "draft-1.txt" in capsys.readouterr().out


def test_four_failed_rounds_fail_the_seed_and_force_starts_over(base):
    run(base, 8)
    for round_ in range(1, 5):
        draft(base, 8, "[Куплет]\nне то\n", round_)
        code = run(base, 8)
    assert code == 1
    assert json.loads((folder(base, 8) / "state.json").read_text())["status"] == "failed"
    assert run(base, 8) == 1
    assert run(base, 8, "--force") == 3


def test_a_refusal_is_recorded_as_such(base):
    run(base, 9)
    draft(base, 9, "REFUSED: not this topic\n")
    assert run(base, 9) == 1
    assert json.loads((folder(base, 9) / "state.json").read_text())["status"] == "refused"


def test_another_topic_for_a_started_seed_is_refused(base):
    run(base, 10, "--topic", "зима")
    assert run(base, 10, "--topic", "лето") == 2
    assert run(base, 10) == 3


def test_a_changed_profile_stops_a_pending_seed(base):
    run(base, 11)
    path = base[1] / "profiles" / "target.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["meta"]["digest"] = "changed"
    path.write_text(json.dumps(profile), encoding="utf-8")
    assert run(base, 11) == 2
    assert run(base, 11, "--force") == 3


def test_an_index_without_the_owners_texts_is_refused(base):
    root, build = base
    assert (
        main(
            [
                "lyrics",
                "index",
                "--lyrics",
                str(root),
                "--build",
                str(build),
                "--without-mine",
                "--force",
            ]
        )
        == 0
    )
    assert run(base, 12) == 2


def test_a_generated_folder_git_would_track_is_refused(base, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert run(base, 13) == 2


def test_saved_texts_join_the_copy_index(base):
    run(base, 5)
    draft(base, 5, good_draft(recipe_of(base, 5)))
    assert run(base, 5) == 0
    root, build = base
    main(["lyrics", "index", "--lyrics", str(root), "--build", str(build)])
    assert "generated" in FingerprintIndex.load(build).artists


def test_a_seed_must_be_a_whole_number_from_zero(base):
    assert run(base, -1) == 2


def test_excerpts_skip_guest_sections_and_borrowed_lines():
    from redline.lyrics.fingerprint import new_key
    from redline.lyrics.generate import excerpts

    borrowed = "холодный ветер дует в спину и снова ночь"
    others = [("fav", parse_text(f"[Куплет]\n{borrowed}\nи больше ничего\n"))]
    own = parse_text(
        "[Куплет]\nпервая своя строка\nвторая своя строка\nтретья своя строка\nчетвертая своя\n"
        f"[Куплет 2]\nмоя строка раз\n{borrowed}\nмоя строка три\nмоя строка четыре\n"
    )
    guest = parse_text("[Куплет: Гость]\nгость поет раз\nгость поет два\nгость три\nгость четыре\n")
    index = FingerprintIndex.build([*others, ("mine", own), ("mine", guest)], new_key())
    picked = excerpts([own, guest], index, seed=1)
    assert picked == ("первая своя строка\nвторая своя строка\nтретья своя строка\nчетвертая своя",)
