"""The marks page: every saved text line by line, each line marked cringe or good."""

import threading
from http import HTTPStatus
from pathlib import Path

from redline.lyrics.marks.store import MARKS, DamagedMarks, read_marks, set_mark
from redline.lyrics.write.generate import versions
from redline.web.local import BadRequest, LocalHandler, LocalServer, only, site_files

ROOT = Path(__file__).resolve().parents[3]
SITE = ROOT / "lyrics-web"
# one look across the pages: the studio's colours and type
TOKENS = ROOT / "studio-web" / "css" / "tokens.css"


def _kind(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return "blank"
    if stripped.startswith("[") and stripped.endswith("]"):
        return "section"
    return "line"


# a seed or version past this is no folder name the writer makes
LARGEST = 10**9


def _number(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < LARGEST:
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"{name} is a whole number from 0")
    return value


def _query_number(query: dict[str, list[str]], name: str) -> int:
    given = query.get(name, [])
    raw = given[0] if len(given) == 1 else ""
    if not (raw.isascii() and raw.isdigit() and len(raw) < 10):
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"{name} is a whole number from 0")
    return int(raw)


class Handler(LocalHandler):
    """Answers the marks page; the folder of generated texts sits on the server."""

    server: "MarksServer"  # pyright: ignore[reportIncompatibleVariableOverride]

    def _text(self, seed: int, version: int) -> tuple[Path, list[str]]:
        folder = self.server.generated / str(seed)
        path = versions(folder).get(version)
        if path is None:
            raise BadRequest(HTTPStatus.NOT_FOUND, f"seed {seed} has no version {version}")
        return folder, path.read_text(encoding="utf-8").splitlines()

    def api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """The saved texts, and one text by seed and version."""
        if path == "/api/texts":
            return self.json(HTTPStatus.OK, self._texts())
        if path == "/api/text":
            seed, version = _query_number(query, "seed"), _query_number(query, "version")
            folder, lines = self._text(seed, version)
            return self.json(
                HTTPStatus.OK,
                {
                    "seed": seed,
                    "version": version,
                    "lines": [{"text": line, "kind": _kind(line)} for line in lines],
                    "marks": read_marks(folder, version),
                },
            )
        return self.error(HTTPStatus.NOT_FOUND, "no such call")

    def _texts(self) -> list[dict]:
        generated = self.server.generated
        seeds = [p for p in generated.iterdir() if p.is_dir() and p.name.isdigit()]
        out = []
        for folder in sorted(seeds, key=lambda p: int(p.name), reverse=True):
            for version in sorted(versions(folder), reverse=True):
                marked = len(read_marks(folder, version))
                out.append({"seed": int(folder.name), "version": version, "marked": marked})
        return out

    def api_post(self, path: str, body: dict) -> None:
        """Set or take off one line's mark."""
        if path != "/api/mark":
            return self.error(HTTPStatus.NOT_FOUND, "no such call")
        only(body, "seed", "version", "line", "mark")
        seed, version = _number(body.get("seed"), "seed"), _number(body.get("version"), "version")
        line, mark = body.get("line"), body.get("mark")
        if mark is not None and mark not in MARKS:
            raise BadRequest(HTTPStatus.BAD_REQUEST, f"a mark is one of {', '.join(MARKS)}")
        folder, lines = self._text(seed, version)
        if not isinstance(line, str) or _kind(line) != "line" or line not in lines:
            raise BadRequest(HTTPStatus.BAD_REQUEST, "the text holds no such line")
        # each request has its own thread; a mark reads the file and writes it back
        with self.server.writing:
            try:
                marks = set_mark(folder, version, line, mark)
            except DamagedMarks as error:
                raise BadRequest(HTTPStatus.CONFLICT, str(error)) from error
        return self.json(HTTPStatus.OK, {"marks": marks})


class MarksServer(LocalServer):
    """The HTTP server with the generated texts' folder and its token."""

    def __init__(self, address: tuple[str, int], generated: Path, token: str) -> None:
        """Bind and keep the folder."""
        files = site_files(SITE)
        files["/css/tokens.css"] = (TOKENS, "text/css; charset=utf-8")
        super().__init__(address, Handler, token, files)
        self.generated = generated
        self.writing = threading.Lock()


def make_server(generated: Path, token: str, port: int = 0) -> MarksServer:
    """A server on 127.0.0.1 only; port 0 takes a free one."""
    return MarksServer(("127.0.0.1", port), generated, token)
