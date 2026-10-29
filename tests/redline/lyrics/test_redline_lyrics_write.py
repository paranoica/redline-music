import json
import subprocess

import pytest
from redline.cli import main
from redline.lyrics.base.fingerprint import FingerprintIndex
from redline.lyrics.text.parse import parse_text
from redline.lyrics.text.syllables import count_syllables
from redline.lyrics.write.recipe import recipe_from_json

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
    from redline.lyrics.base.fingerprint import new_key
    from redline.lyrics.write.generate import excerpts

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


def test_a_saved_seed_asked_from_another_profile_is_refused(base):
    run(base, 21)
    draft(base, 21, good_draft(recipe_of(base, 21)))
    assert run(base, 21) == 0
    assert run(base, 21, "--profile", "working") == 2


def test_the_copy_threshold_cannot_be_loosened(base):
    assert run(base, 22, "--min-words", "9") == 2
    assert run(base, 22, "--min-words", "5") == 3
    state = json.loads((folder(base, 22) / "state.json").read_text(encoding="utf-8"))
    assert state["min_words"] == 5


def test_a_profile_from_before_stress_was_recorded_is_refused(base):
    path = base[1] / "profiles" / "target.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    del profile["meta"]["stress"]
    path.write_text(json.dumps(profile), encoding="utf-8")
    assert run(base, 23) == 2


def test_the_drawn_topic_can_be_named_again_and_force_takes_a_new_one(base):
    run(base, 24)
    drawn = recipe_of(base, 24).topic
    assert run(base, 24, "--topic", drawn) == 3
    assert run(base, 24, "--topic", "зима", "--force") == 3
    assert recipe_of(base, 24).topic == "зима"


def test_a_round_that_is_not_a_number_is_refused(base):
    run(base, 25)
    path = folder(base, 25) / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["round"] = "../x"
    path.write_text(json.dumps(state), encoding="utf-8")
    assert run(base, 25) == 2


def test_seed_files_are_readable_by_the_owner_only(base):
    run(base, 26)
    for path in folder(base, 26).iterdir():
        assert path.stat().st_mode & 0o077 == 0, path.name


def test_each_round_records_the_prompt_it_was_written_under(base):
    run(base, 27)
    draft(base, 27, "[Куплет]\nне то\n")
    run(base, 27)
    checks = json.loads((folder(base, 27) / "checks-1.json").read_text(encoding="utf-8"))
    assert len(checks["prompt"]) == 12


def test_private_style_notes_go_into_the_request(base):
    (base[0] / "style.md").write_text("заметка про стиль сцены\n", encoding="utf-8")
    run(base, 31)
    request = (folder(base, 31) / "request-1.md").read_text(encoding="utf-8")
    assert "заметка про стиль сцены" in request


def test_each_seed_sees_its_own_part_of_the_style_notes():
    from redline.lyrics.write.generate import style_for

    names = [
        "альфа",
        "бета",
        "гамма",
        "дельта",
        "эпсилон",
        "зета",
        "эта",
        "тета",
        "йота",
        "каппа",
        "лямбда",
        "мю",
    ]
    notes = f"Заголовок:\n- Бренды: {', '.join(names)}\n- Приём без списка\n"
    one, two = style_for(notes, 1), style_for(notes, 2)
    assert style_for(notes, 1) == one
    assert "Заголовок:" in one and "- Приём без списка" in one
    kept = one.split("\n")[1].split(": ")[1].split(", ")
    assert 3 <= len(kept) < len(names) and set(kept) <= set(names)
    assert one != two


def test_style_notes_keep_brackets_and_prose_whole():
    from redline.lyrics.write.generate import style_for

    listed = (
        "- Бренды: Rick Owens (DRKSHDW, Geobasket), Number (N)ine, Undercover, Kapital, "
        "Visvim, Julius, Guidi, Acne, Sacai, Loewe, Alyx"
    )
    prose = (
        "- Юмор: говори ровно, без улыбки, как факт, как сводку, как прогноз, как бухгалтер, "
        "как диктор"
    )
    for seed in range(20):
        cut = style_for(listed + "\n" + prose, seed).split("\n")
        assert cut[1] == prose
        assert cut[0].count("(") == cut[0].count(")")


def test_style_notes_that_copy_the_base_are_refused(base):
    (base[0] / "style.md").write_text(f"- пример: {SENTINEL} 0\n", encoding="utf-8")
    assert run(base, 32) == 2


def test_each_round_records_the_style_notes_it_used(base):
    (base[0] / "style.md").write_text("- заметка\n", encoding="utf-8")
    run(base, 33)
    draft(base, 33, "[Куплет]\nне то\n")
    run(base, 33)
    checks = json.loads((folder(base, 33) / "checks-1.json").read_text(encoding="utf-8"))
    assert len(checks["style"]) == 12


def test_cli_suno_writes_the_card_of_a_saved_seed(base, capsys):
    run(base, 34)
    draft(base, 34, good_draft(recipe_of(base, 34)))
    assert run(base, 34) == 0
    capsys.readouterr()
    assert main(["lyrics", "suno", "--seed", "34", "--lyrics", str(base[0])]) == 0
    out = capsys.readouterr().out
    assert "Title:" in out and "Lyrics:" in out and "Variety: 0" in out
    assert (folder(base, 34) / "suno.md").read_text(encoding="utf-8") == out
    assert main(["lyrics", "suno", "--seed", "35", "--lyrics", str(base[0])]) == 2


def test_cli_suno_takes_the_owners_style_file(base, capsys):
    run(base, 36)
    draft(base, 36, good_draft(recipe_of(base, 36)))
    assert run(base, 36) == 0
    (base[0] / "suno-style.txt").write_text("свой стиль, 150 bpm\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["lyrics", "suno", "--seed", "36", "--lyrics", str(base[0])]) == 0
    assert "Style: свой стиль, 150 bpm\n" in capsys.readouterr().out
    (base[0] / "suno-style.txt").write_text("\n", encoding="utf-8")
    assert main(["lyrics", "suno", "--seed", "36", "--lyrics", str(base[0])]) == 0
    assert "Style: trap, " in capsys.readouterr().out


def off_draft(recipe):
    # every third line far over its range: about two lines in three fit
    lines = good_draft(recipe).split("\n")
    out, sung = [], 0
    for line in lines:
        if line and not line.startswith("["):
            sung += 1
            if sung % 3 == 0:
                line = "та " * 8 + line
        out.append(line)
    return "\n".join(out)


def test_a_beat_seed_freezes_the_half_time_tempo_and_tells_the_writer(base):
    assert run(base, 40, "--bpm", "160") == 3
    recipe = recipe_of(base, 40)
    assert recipe.bpm == 80.0
    request = (folder(base, 40) / "request-1.md").read_text(encoding="utf-8")
    assert "80 BPM" in request and "one bar" in request
    assert "- Line length:" not in request
    assert all(f"flow {s.flow}" in request for s in recipe.sections)
    assert "- Line length:" in (
        run(base, 41) == 3 and (folder(base, 41) / "request-1.md")
    ).read_text(encoding="utf-8")


def test_a_beat_seed_holds_its_lines_tighter(base):
    run(base, 42, "--bpm", "75")
    draft(base, 42, off_draft(recipe_of(base, 42)))
    assert run(base, 42) == 3
    reasons = json.loads((folder(base, 42) / "state.json").read_text())["reasons"]
    assert any(r.startswith("syllables") for r in reasons)
    run(base, 43)
    draft(base, 43, off_draft(recipe_of(base, 43)))
    assert run(base, 43) == 0


def test_another_tempo_for_a_started_seed_is_refused_and_force_keeps_the_beat(base):
    assert run(base, 44, "--bpm", "150") == 3
    assert run(base, 44, "--bpm", "140") == 2
    assert run(base, 44, "--bpm", "75") == 3
    assert run(base, 44) == 3
    assert run(base, 44, "--force") == 3
    assert recipe_of(base, 44).bpm == 75.0
    run(base, 45)
    assert run(base, 45, "--bpm", "150") == 2
    assert run(base, 45, "--bpm", "150", "--force") == 3
    assert recipe_of(base, 45).bpm == 75.0


def test_a_saved_seed_is_not_given_out_for_another_beat(base):
    run(base, 46, "--bpm", "150")
    draft(base, 46, good_draft(recipe_of(base, 46)))
    assert run(base, 46) == 0
    assert run(base, 46, "--bpm", "75") == 0
    assert run(base, 46, "--bpm", "100") == 2


@pytest.mark.parametrize("bpm", ["0", "-80", "nan", "inf"])
def test_a_tempo_must_be_a_positive_number(base, bpm):
    assert run(base, 47, "--bpm", bpm) == 2
    assert not folder(base, 47).exists()


def test_a_missing_beat_file_and_both_tempo_options_are_refused(base, tmp_path):
    assert run(base, 48, "--beat", str(tmp_path / "none.wav")) == 2
    with pytest.raises(SystemExit):
        run(base, 48, "--beat", str(tmp_path / "none.wav"), "--bpm", "150")


def test_cli_suno_on_a_beat_seed_writes_the_vocal_style(base, capsys):
    run(base, 49, "--bpm", "132")
    draft(base, 49, good_draft(recipe_of(base, 49)))
    assert run(base, 49) == 0
    capsys.readouterr()
    assert main(["lyrics", "suno", "--seed", "49", "--lyrics", str(base[0])]) == 0
    out = capsys.readouterr().out
    assert " flow" in out.split("Style: ")[1].split("\n")[0] and "bpm" not in out


def test_a_measured_beat_freezes_its_bars_and_a_given_tempo_has_none(base, tmp_path):
    import numpy as np
    import soundfile as sf

    rate = 22050
    x = np.zeros(rate * 40, dtype=np.float32)
    for i in range(int(40 * 150 / 60)):
        start = int(i * 60 / 150 * rate)
        x[start : start + 200] = np.hanning(200)
    sf.write(tmp_path / "beat.wav", np.column_stack([x, x]), rate)
    assert run(base, 51, "--beat", str(tmp_path / "beat.wav")) == 3
    recipe = recipe_of(base, 51)
    assert abs(recipe.bpm - 75) <= 2 and recipe.bars == int(40 * recipe.bpm / 240)
    assert sum(s.lines for s in recipe.sections) <= recipe.bars
    assert run(base, 51, "--force") == 3
    assert recipe_of(base, 51).bars == recipe.bars
    run(base, 52, "--bpm", "150")
    assert recipe_of(base, 52).bars is None


def test_a_delivery_reaches_the_recipe_and_the_writer(base):
    assert run(base, 53, "--bpm", "140", "--delivery", "hard") == 3
    assert recipe_of(base, 53).delivery == "hard"
    request = (folder(base, 53) / "request-1.md").read_text(encoding="utf-8")
    assert "Delivery: hard" in request
    assert run(base, 54, "--delivery", "hard") == 2
    assert run(base, 55, "--bpm", "140") == 3
    assert recipe_of(base, 55).delivery is None


def edit(base, seed, *extra):
    root, build = base
    args = ["lyrics", "edit", "--seed", str(seed), "--lyrics", str(root), "--build", str(build)]
    return main([*args, *extra])


def saved_seed(base, seed):
    run(base, seed)
    draft(base, seed, good_draft(recipe_of(base, seed)))
    assert run(base, seed) == 0
    return (folder(base, seed) / "text.txt").read_text(encoding="utf-8")


def test_an_edit_needs_a_saved_seed_and_a_change(base):
    run(base, 60)
    assert edit(base, 60, "усиль хук") == 2
    saved_seed(base, 61)
    assert edit(base, 61) == 2
    assert edit(base, 61, "   ") == 2


def test_an_edit_asks_the_writer_with_the_text_and_the_change(base):
    text = saved_seed(base, 62)
    assert edit(base, 62, "усиль хук") == 3
    request = (folder(base, 62) / "edit-2" / "request-1.md").read_text(encoding="utf-8")
    assert "усиль хук" in request and text.splitlines()[1] in request
    assert SENTINEL not in request


def test_a_good_edit_is_the_next_version_and_suno_takes_the_newest(base, capsys):
    text = saved_seed(base, 63)
    edit(base, 63, "поменяй первую строку")
    changed = text.replace(text.splitlines()[1], "та та та та та та та новая строчка тут", 1)
    (folder(base, 63) / "edit-2" / "draft-1.txt").write_text(changed, encoding="utf-8")
    assert edit(base, 63) == 0
    assert (folder(base, 63) / "text-2.txt").read_text(encoding="utf-8") == changed
    assert (folder(base, 63) / "text.txt").read_text(encoding="utf-8") == text
    capsys.readouterr()
    lyrics = ["lyrics", "suno", "--seed", "63", "--lyrics", str(base[0])]
    assert main(lyrics) == 0
    assert "новая строчка тут" in capsys.readouterr().out
    assert main([*lyrics, "--version", "1"]) == 0
    assert "новая строчка тут" not in capsys.readouterr().out
    assert main([*lyrics, "--version", "7"]) == 2


def test_an_edit_that_copies_the_base_goes_back(base):
    text = saved_seed(base, 64)
    edit(base, 64, "добавь строку")
    copied = text.replace(text.splitlines()[1], f"{SENTINEL} 0", 1)
    (folder(base, 64) / "edit-2" / "draft-1.txt").write_text(copied, encoding="utf-8")
    assert edit(base, 64) == 3
    assert (folder(base, 64) / "edit-2" / "request-2.md").exists()
    assert not (folder(base, 64) / "text-2.txt").exists()


def test_a_second_edit_starts_from_the_newest_version_and_a_pending_one_is_kept(base):
    text = saved_seed(base, 65)
    edit(base, 65, "раз")
    second = text.replace(text.splitlines()[1], "вторая версия этой строки целиком тут", 1)
    (folder(base, 65) / "edit-2" / "draft-1.txt").write_text(second, encoding="utf-8")
    assert edit(base, 65) == 0
    assert edit(base, 65, "два") == 3
    assert edit(base, 65, "три") == 2
    request = (folder(base, 65) / "edit-3" / "request-1.md").read_text(encoding="utf-8")
    assert "вторая версия этой строки" in request
    assert edit(base, 65, "три", "--force") == 3
    assert "три" in (folder(base, 65) / "edit-3" / "request-1.md").read_text(encoding="utf-8")


def test_another_delivery_for_a_started_seed_is_refused(base):
    assert run(base, 66, "--bpm", "140", "--delivery", "calm") == 3
    assert run(base, 66, "--bpm", "140", "--delivery", "hard") == 2
    assert run(base, 66, "--bpm", "140", "--delivery", "calm") == 3
    assert run(base, 66) == 3


def test_an_edit_is_not_a_copy_of_its_own_seed_after_the_index_is_rebuilt(base):
    saved_seed(base, 67)
    # words that do not collapse, so a kept line is a real eight-word run
    text = (
        "[Куплет]\nпервая строка где восемь разных слов стоят подряд тут\n"
        "вторая строчка тоже держит восемь слов совсем других здесь\n"
    )
    (folder(base, 67) / "text.txt").write_text(text, encoding="utf-8")
    root, build = base
    assert main(["lyrics", "index", "--lyrics", str(root), "--build", str(build), "--force"]) == 0
    edit(base, 67, "поменяй одну строку")
    changed = text.replace(text.splitlines()[1], "новая первая строка совсем другая", 1)
    (folder(base, 67) / "edit-2" / "draft-1.txt").write_text(changed, encoding="utf-8")
    assert edit(base, 67) == 0


def test_an_edit_refuses_another_profile_and_a_damaged_state(base):
    saved_seed(base, 68)
    assert edit(base, 68, "раз", "--profile", "working") in (2,)
    edit(base, 68, "раз")
    (folder(base, 68) / "edits.json").write_text(
        '{"done": [], "pending": {"round": "x"}}', encoding="utf-8"
    )
    assert edit(base, 68) == 2


def test_a_second_round_of_an_edit_shows_the_draft_sent_back(base):
    text = saved_seed(base, 69)
    edit(base, 69, "добавь строку")
    copied = text.replace(text.splitlines()[1], f"{SENTINEL} 0", 1)
    (folder(base, 69) / "edit-2" / "draft-1.txt").write_text(copied, encoding="utf-8")
    assert edit(base, 69) == 3
    request = (folder(base, 69) / "edit-2" / "request-2.md").read_text(encoding="utf-8")
    assert "draft that was sent back" in request.lower()


def test_suno_version_zero_is_refused(base):
    saved_seed(base, 70)
    assert main(["lyrics", "suno", "--seed", "70", "--lyrics", str(base[0]), "--version", "0"]) == 2


def test_a_seed_from_before_deliveries_takes_one(base):
    assert run(base, 71, "--bpm", "140") == 3
    path = folder(base, 71) / "state.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    del state["delivery"]
    path.write_text(json.dumps(state), encoding="utf-8")
    assert run(base, 71, "--bpm", "140", "--delivery", "hard") == 3
