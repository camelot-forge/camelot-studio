"""Serve a feed, with the public URL written on the way out (`F §D3`).

The feed on disk is environment-agnostic: the operator publishes it once, and the
same directory is served locally and online. This server owns the one thing that
changes — the base URL in `patchlist-new.txt` and the game address in
`launcher.json` — so the container's environment is the only switch.

It is the server-side half, not the players' one: it reads `feed`'s file names and
serves files, and nothing of the Studio, the project or a database travels here.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import feed

__all__ = ["DEFAULT_PORT", "handler_for", "main", "serve", "server_for"]

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080


def _with_slash(value: str) -> str:
    return value if not value or value.endswith("/") else value + "/"


def handler_for(feed_dir, feed_url: str, game_server: str):
    """A handler that serves *feed_dir* and rewrites the two environment-specific
    files per request. A missing `launcher.json` stays a 404: the protocol allows
    a feed without our own record, and the launcher says so."""
    where = Path(feed_dir).expanduser().resolve()
    feed_url = _with_slash(str(feed_url))

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(where), **kwargs)

        def list_directory(self, path):
            self.send_error(404, "not a directory listing")

        def _bytes(self, body: bytes, content_type: str, *, head: bool = False) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _patchlist(self, *, head: bool = False) -> None:
            path = where / feed.PATCHLIST_FILE
            if not path.is_file():
                self.send_error(404, f"no {feed.PATCHLIST_FILE}")
                return
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines:
                self.send_error(500, f"{feed.PATCHLIST_FILE} is empty")
                return
            lines[0] = feed_url
            self._bytes(
                ("\n".join(lines) + "\n").encode("utf-8"),
                "text/plain; charset=utf-8",
                head=head,
            )

        def _launcher(self, *, head: bool = False) -> None:
            path = where / feed.LAUNCHER_FILE
            if not path.is_file():
                self.send_error(404, f"no {feed.LAUNCHER_FILE}")
                return
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                self.send_error(500, f"{feed.LAUNCHER_FILE}: {error}")
                return
            if not isinstance(document, dict):
                self.send_error(500, f"{feed.LAUNCHER_FILE} is not an object")
                return
            document["feed"] = feed_url
            document["server"] = str(game_server)
            body = (json.dumps(document, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
            self._bytes(body, "application/json; charset=utf-8", head=head)

        def _health(self, *, head: bool = False) -> None:
            body = json.dumps(
                {
                    "ok": True,
                    "feed": feed_url,
                    "server": str(game_server),
                    "path": str(where),
                }
            ).encode("utf-8")
            self._bytes(body, "application/json; charset=utf-8", head=head)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/healthz":
                return self._health()
            if path == f"/{feed.PATCHLIST_FILE}":
                return self._patchlist()
            if path == f"/{feed.LAUNCHER_FILE}":
                return self._launcher()
            return super().do_GET()

        def do_HEAD(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/healthz":
                return self._health(head=True)
            if path == f"/{feed.PATCHLIST_FILE}":
                return self._patchlist(head=True)
            if path == f"/{feed.LAUNCHER_FILE}":
                return self._launcher(head=True)
            return super().do_HEAD()

    return Handler


def server_for(
    feed_dir,
    feed_url: str,
    game_server: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> ThreadingHTTPServer:
    """A bound server, so a test can drive the routes without a container."""
    return ThreadingHTTPServer((host, port), handler_for(feed_dir, feed_url, game_server))


def serve(
    feed_dir,
    feed_url: str,
    game_server: str,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> int:
    server = server_for(feed_dir, feed_url, game_server, host=host, port=port)
    seen = f"http://{host}:{server.server_port}/"
    print(f"camelot feed · {seen} · {feed_url} · {game_server}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv=None) -> int:
    """The container's entry point: the environment is the whole configuration."""
    feed_dir = os.environ.get("CAMELOT_FEED_DIR") or "/feed"
    feed_url = _with_slash(os.environ.get("CAMELOT_FEED_URL") or "")
    game_server = os.environ.get("CAMELOT_GAME_SERVER") or ""
    host = os.environ.get("CAMELOT_FEED_HOST") or DEFAULT_HOST
    try:
        port = int(os.environ.get("CAMELOT_FEED_PORT") or DEFAULT_PORT)
    except ValueError:
        print("error: CAMELOT_FEED_PORT is not an integer", file=sys.stderr)
        return 2
    if not feed_url:
        print("error: CAMELOT_FEED_URL is not set", file=sys.stderr)
        return 2
    if not game_server:
        print("error: CAMELOT_GAME_SERVER is not set", file=sys.stderr)
        return 2
    where = Path(feed_dir).expanduser()
    missing = [
        name
        for name in (feed.VERSION_FILE, feed.PATCHLIST_FILE)
        if not (where / name).is_file()
    ]
    if missing:
        print(
            f"error: {where} is not a feed (missing {', '.join(missing)})",
            file=sys.stderr,
        )
        return 2
    return serve(where, feed_url, game_server, host=host, port=port)


if __name__ == "__main__":
    raise SystemExit(main())
