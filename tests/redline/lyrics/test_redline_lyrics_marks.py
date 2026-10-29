import http.client
import json
import os
import threading

import pytest
from redline.lyrics.marks import server as marks_server
from redline.lyrics.marks.store import MARKS, marked_lines, read_marks, set_mark

TOKEN = "t" * 40
TEXT = "[Припев]\nснег идёт (е)\nснег идёт (е)\n\n[Куплет]\nпервая строка\nвторая строка\n"


def seed(root, number, text=TEXT, version=1):
    folder = root / str(number)
    folder.mkdir(parents=True, exist_ok=True)
    name = "text.txt" if version == 1 else f"text-{version}.txt"
    (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_a_mark_is_kept_changed_and_taken_off(tmp_path):
    folder = seed(tmp_path, 1)
    set_mark(folder, 1, "первая строка", "cringe")
    set_mark(folder, 1, "вторая строка", "good")
    assert read_marks(folder, 1) == {"первая строка": "cringe", "вторая строка": "good"}
    set_mark(folder, 1, "первая строка", "good")
    set_mark(folder, 1, "вторая строка", None)
    assert read_marks(folder, 1) == {"первая строка": "good"}
    assert ((folder / "marks-1.json").stat().st_mode & 0o777) == 0o600
    assert read_marks(folder, 2) == {}


@pytest.mark.parametrize("mark", ["meh", "", "CRINGE"])
def test_only_the_known_marks_are_taken(tmp_path, mark):
    folder = seed(tmp_path, 1)
    with pytest.raises(ValueError):
        set_mark(folder, 1, "первая строка", mark)
    assert MARKS == ("cringe", "good")


def test_a_damaged_marks_file_reads_as_no_marks(tmp_path):
    folder = seed(tmp_path, 1)
    for broken in ("{", "[1, 2]", json.dumps({"строка": "meh", "другая": 3})):
        (folder / "marks-1.json").write_text(broken, encoding="utf-8")
        assert read_marks(folder, 1) == {}
    assert marked_lines(tmp_path) == ((), ())


def test_marked_lines_put_the_newest_first_once_each_and_keep_to_the_limits(tmp_path):
    old, new = seed(tmp_path, 1), seed(tmp_path, 2)
    for i in range(5):
        set_mark(old, 1, f"старая {i}", "cringe")
    set_mark(new, 1, "новая плохая", "cringe")
    set_mark(new, 1, "новая хорошая", "good")
    set_mark(new, 1, "старая 0", "cringe")
    os.utime(old / "marks-1.json", (1, 1))
    cringe, good = marked_lines(tmp_path, cringe=3, good=10)
    assert cringe == ("старая 0", "новая плохая", "старая 4")
    assert good == ("новая хорошая",)
    assert marked_lines(tmp_path / "missing") == ((), ())


def test_a_line_marked_again_counts_as_its_newest_mark(tmp_path):
    folder = seed(tmp_path, 1)
    set_mark(folder, 1, "первая строка", "cringe")
    set_mark(folder, 1, "первая строка", "good")
    assert marked_lines(tmp_path) == ((), ("первая строка",))


@pytest.fixture
def server(tmp_path):
    seed(tmp_path, 3)
    seed(tmp_path, 3, TEXT.replace("первая", "новая"), version=2)
    seed(tmp_path, 12)
    (tmp_path / "notes").mkdir()
    httpd = marks_server.make_server(tmp_path, TOKEN, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def call(server, method, path, body=None, token=TOKEN, host=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    sent = {"Host": host or f"127.0.0.1:{port}"}
    if token is not None:
        sent["X-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=sent)
    response = conn.getresponse()
    raw = response.read()
    return response.status, json.loads(raw) if raw.startswith((b"{", b"[")) else raw


def test_the_page_lists_every_saved_text_newest_seed_first(server):
    status, texts = call(server, "GET", "/api/texts")
    assert status == 200
    assert texts == [
        {"seed": 12, "version": 1, "marked": 0},
        {"seed": 3, "version": 2, "marked": 0},
        {"seed": 3, "version": 1, "marked": 0},
    ]


def test_a_text_comes_as_lines_with_their_kinds_and_marks(server, tmp_path):
    set_mark(tmp_path / "3", 2, "новая строка", "cringe")
    status, text = call(server, "GET", "/api/text?seed=3&version=2")
    assert status == 200
    kinds = [(line["text"], line["kind"]) for line in text["lines"]]
    assert kinds[:3] == [
        ("[Припев]", "section"),
        ("снег идёт (е)", "line"),
        ("снег идёт (е)", "line"),
    ]
    assert ("", "blank") in kinds and ("новая строка", "line") in kinds
    assert text["marks"] == {"новая строка": "cringe"}


def test_a_mark_from_the_page_is_saved_for_that_version(server, tmp_path):
    body = {"seed": 3, "version": 1, "line": "первая строка", "mark": "cringe"}
    status, answer = call(server, "POST", "/api/mark", body)
    assert status == 200 and answer["marks"] == {"первая строка": "cringe"}
    assert read_marks(tmp_path / "3", 1) == {"первая строка": "cringe"}
    status, answer = call(server, "POST", "/api/mark", {**body, "mark": None})
    assert status == 200 and read_marks(tmp_path / "3", 1) == {}
    status, texts = call(server, "GET", "/api/texts")
    assert texts[2]["marked"] == 0


@pytest.mark.parametrize(
    "body",
    [
        {"seed": 3, "version": 1, "line": "чужая строка", "mark": "cringe"},
        {"seed": 3, "version": 1, "line": "[Припев]", "mark": "cringe"},
        {"seed": 3, "version": 1, "line": "", "mark": "cringe"},
        {"seed": 3, "version": 1, "line": "первая строка", "mark": "meh"},
        {"seed": 3, "version": 9, "line": "первая строка", "mark": "cringe"},
        {"seed": 99, "version": 1, "line": "первая строка", "mark": "cringe"},
        {"seed": "3", "version": 1, "line": "первая строка", "mark": "cringe"},
        {"seed": True, "version": 1, "line": "первая строка", "mark": "cringe"},
        {"seed": 3, "version": 1, "line": "первая строка", "mark": "cringe", "path": "x"},
    ],
)
def test_a_mark_the_text_does_not_hold_is_refused(server, tmp_path, body):
    status, _ = call(server, "POST", "/api/mark", body)
    assert status in (400, 404)
    assert not list(tmp_path.glob("*/marks-*.json"))


@pytest.mark.parametrize(
    "query", ["seed=3", "seed=../3&version=1", "seed=3&version=x", "seed=99&version=1"]
)
def test_a_text_that_is_not_there_is_refused(server, query):
    status, _ = call(server, "GET", f"/api/text?{query}")
    assert status in (400, 404)


def test_the_marks_page_keeps_the_studios_guards(server):
    assert call(server, "GET", "/api/texts", token=None)[0] == 403
    assert call(server, "GET", "/api/texts", token="wrong")[0] == 403
    assert call(server, "GET", "/api/texts", host="evil.example:80")[0] == 403
    assert call(server, "GET", "/", token=None)[0] == 403
    assert call(server, "GET", f"/?t={TOKEN}", token=None)[0] == 200
    body = {"seed": 3, "version": 1, "line": "первая строка", "mark": "good"}
    assert call(server, "POST", "/api/mark", body, token=None)[0] == 403
    assert call(server, "GET", "/../text.txt")[0] == 404


def test_the_page_serves_its_own_files_and_the_studios_tokens(server):
    files = server.files
    assert "/index.html" in files and "/css/tokens.css" in files
    assert all(path.suffix in (".html", ".css", ".js", ".svg") for path, _ in files.values())


def test_cli_marks_prints_a_local_address_with_a_token_and_the_seed(tmp_path, capsys, monkeypatch):
    from redline.cli import main

    seed(tmp_path / "lyrics" / "generated", 5)
    started = {}

    class Fake:
        server_address = ("127.0.0.1", 6060)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            started["closed"] = True

    def fake_make(generated, token, port=0):
        started.update(generated=generated, token=token)
        return Fake()

    monkeypatch.setattr(marks_server, "make_server", fake_make)
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    args = ["lyrics", "marks", "--lyrics", str(tmp_path / "lyrics"), "--seed", "5"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert f"http://127.0.0.1:6060/?t={started['token']}#seed=5" in out
    assert len(started["token"]) >= 32 and started["closed"]
    assert started["generated"] == tmp_path / "lyrics" / "generated"
    assert len(opened) == 1 and started["token"] not in opened[0]


def test_cli_marks_without_saved_texts_exits_2(tmp_path):
    from redline.cli import main

    assert main(["lyrics", "marks", "--lyrics", str(tmp_path / "none"), "--no-browser"]) == 2
