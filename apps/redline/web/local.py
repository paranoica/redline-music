"""A server on 127.0.0.1 for one page: a token, this machine's Host only, no outside calls.

Every API call carries the token printed with the page address, and the Host must be this
machine at this port, so neither another site in the browser nor a rebound name gets in.
"""

import contextlib
import hmac
import html
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

KINDS = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}
MAX_BODY = 64 * 1024
HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; media-src 'self' blob:; img-src 'self' data:; "
        "style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com"
    ),
}


def site_files(root: Path) -> dict[str, tuple[Path, str]]:
    """Every page file by its URL path, listed once: nothing outside this map is served."""
    files = {
        "/" + path.relative_to(root).as_posix(): (path, KINDS[path.suffix])
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in KINDS
    }
    files["/"] = files["/index.html"]
    return files


class BadRequest(Exception):
    """The request asks for something the page's server does not take."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        """Keep the status to answer with."""
        super().__init__(message)
        self.status = status


def only(body: dict, *keys: str) -> None:
    """Refuse a body with keys other than these."""
    extra = sorted(set(body) - set(keys))
    if extra:
        raise BadRequest(HTTPStatus.BAD_REQUEST, f"unexpected {', '.join(extra)}")


class LocalServer(ThreadingHTTPServer):
    """The HTTP server with its token and the page's files."""

    daemon_threads = True
    # the default backlog of five refuses a burst of quick clicks
    request_queue_size = 64

    def __init__(
        self,
        address: tuple[str, int],
        handler: type["LocalHandler"],
        token: str,
        files: dict[str, tuple[Path, str]],
    ) -> None:
        """Bind and keep the token and the files."""
        super().__init__(address, handler)
        self.token = token
        self.files = files


class LocalHandler(BaseHTTPRequestHandler):
    """The guards and the plumbing; a page's own calls go in `api_get` and `api_post`."""

    server: LocalServer  # pyright: ignore[reportIncompatibleVariableOverride]
    # a connection that sends nothing must not hold a thread for good
    timeout = 10

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the console for the page address and what the page reports."""

    def send(self, status: int, body: bytes, kind: str, extra: dict | None = None) -> None:
        """Answer with the guard headers on."""
        self.send_response(status)
        for name, value in {**HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # the page drops a request a newer one replaced; its closed socket is not an error
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def json(self, status: int, data: Any) -> None:
        """Answer with JSON."""
        self.send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def error(self, status: int, message: str) -> None:
        """Answer with an error the page shows."""
        self.json(status, {"error": message})

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, given: str | None) -> bool:
        return given is not None and hmac.compare_digest(given.encode(), self.server.token.encode())

    def do_GET(self) -> None:
        """The page, its static files and the read-only API."""
        if not self._host_ok():
            return self.error(HTTPStatus.FORBIDDEN, "wrong host")
        url = urlsplit(self.path)
        if url.path in ("/", "/index.html"):
            given = parse_qs(url.query).get("t", [None])[0]
            if not self._token_ok(given):
                return self.error(HTTPStatus.FORBIDDEN, "open the address that was printed")
        if url.path in self.server.files:
            path, kind = self.server.files[url.path]
            return self.send(HTTPStatus.OK, path.read_bytes(), kind)
        if not url.path.startswith("/api/"):
            return self.error(HTTPStatus.NOT_FOUND, "no such page")
        if not self._token_ok(self.headers.get("X-Token")):
            return self.error(HTTPStatus.FORBIDDEN, "no token")
        try:
            return self.api_get(url.path, parse_qs(url.query))
        except BadRequest as error:
            return self.error(error.status, str(error))

    def do_POST(self) -> None:
        """The API calls that change something."""
        if not self._host_ok():
            return self.error(HTTPStatus.FORBIDDEN, "wrong host")
        if not self._token_ok(self.headers.get("X-Token")):
            return self.error(HTTPStatus.FORBIDDEN, "no token")
        try:
            return self.api_post(urlsplit(self.path).path, self._body())
        except BadRequest as error:
            return self.error(error.status, str(error))

    def _body(self) -> dict:
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise BadRequest(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")
        if self.headers.get("Transfer-Encoding"):
            raise BadRequest(HTTPStatus.LENGTH_REQUIRED, "no chunked bodies")
        length = self.headers.get("Content-Length", "")
        if not (length.isascii() and length.isdigit()):
            raise BadRequest(HTTPStatus.LENGTH_REQUIRED, "Content-Length is needed")
        if int(length) > MAX_BODY:
            self.close_connection = True
            raise BadRequest(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the body is too large")
        try:
            body = json.loads(self.rfile.read(int(length)))
        except ValueError as error:
            raise BadRequest(HTTPStatus.BAD_REQUEST, "the body is not JSON") from error
        if not isinstance(body, dict):
            raise BadRequest(HTTPStatus.BAD_REQUEST, "the body is a JSON object")
        return body

    def api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """Answer a GET under /api/."""
        self.error(HTTPStatus.NOT_FOUND, "no such call")

    def api_post(self, path: str, body: dict) -> None:
        """Answer a POST."""
        self.error(HTTPStatus.NOT_FOUND, "no such call")


def browser_file(url: str, folder: Path) -> Path:
    """A page only the owner can read that sends the browser to url.

    Opening the address itself would put the token on the browser's command line, which
    every user of the machine can read.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "open.html"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    page = f'<!doctype html><meta http-equiv="refresh" content="0;url={html.escape(url)}">'
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(page)
    return path
