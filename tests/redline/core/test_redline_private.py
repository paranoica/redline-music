import stat
import threading

from redline.core.private import write_private


def test_concurrent_writes_to_one_file_all_succeed_and_leave_one_whole_text(tmp_path):
    path = tmp_path / "shared.json"
    texts = [f"{n:02d}" * 2000 for n in range(8)]
    errors = []
    start = threading.Barrier(len(texts))

    def writer(text):
        start.wait()
        for _ in range(100):
            try:
                write_private(path, text)
            except OSError as error:
                errors.append(error)

    threads = [threading.Thread(target=writer, args=(text,)) for text in texts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert path.read_text(encoding="utf-8") in texts
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["shared.json"]


def test_a_write_makes_an_open_file_private_and_leaves_no_temporary(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o644)
    write_private(path, "new")
    assert path.read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["report.md"]
