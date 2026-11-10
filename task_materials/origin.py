"""Synthetic loopback HTTP origin for exercising representation-aware caches.

This fixture contains no cache. Each request is served afresh and recorded.
Run ``python task_materials/origin.py --port 0`` or use ``running_origin()``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator
from urllib.parse import urlsplit


class OriginFixture:
    """A local HTTP server with controllable Vary declarations and request log.

    Header names in ``request_log`` are lowercase. Values remain ordered lists;
    repeated field lines and explicitly empty values are retained. ``selected``
    in response JSON uses ``None`` for an absent selection field.
    """

    def __init__(self, port: int = 0) -> None:
        self.request_log: list[dict] = []
        self._mode = "region"
        self._sequence = 0
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                self._respond(send_body=True)

            def do_HEAD(self) -> None:
                self._respond(send_body=False)

            def _respond(self, *, send_body: bool) -> None:
                path = urlsplit(self.path).path
                headers: dict[str, list[str]] = {}
                for name, value in self.headers.raw_items():
                    headers.setdefault(name.lower(), []).append(value)
                status, response_headers, body = owner._response(path, headers)
                with owner._lock:
                    owner.request_log.append(
                        {
                            "method": self.command,
                            "path": path,
                            "target": self.path,
                            "headers": headers,
                            "status": status,
                        }
                    )
                self.send_response(status)
                for name, value in response_headers.items():
                    self.send_header(name, value)
                if status != 304:
                    self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if send_body and status != 304:
                    self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"

    def set_mode(self, mode: str) -> None:
        """Change /switch to region, language, plain, or star selection."""
        if mode not in {"region", "language", "plain", "star"}:
            raise ValueError(f"Unsupported origin mode: {mode}")
        with self._lock:
            self._mode = mode

    def start(self) -> OriginFixture:
        if self._thread is not None:
            raise RuntimeError("Origin has already been started")
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="vary-origin",
            daemon=True,
        )
        self._thread.start()
        return self

    def close(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
        self._server.server_close()

    def __enter__(self) -> OriginFixture:
        return self.start()

    def __exit__(self, *args: object) -> None:
        self.close()

    def _response(
        self, path: str, headers: dict[str, list[str]]
    ) -> tuple[int, dict[str, str], bytes]:
        with self._lock:
            mode = self._mode
            self._sequence += 1
            sequence = self._sequence

        if path not in {"/article", "/revalidate", "/star", "/plain", "/switch"}:
            return 404, {"Cache-Control": "no-store"}, b"Unknown fixture resource\n"

        if path in {"/article", "/revalidate"}:
            fields = ["accept-language", "x-region"]
        elif path == "/switch" and mode == "region":
            fields = ["x-region"]
        elif path == "/switch" and mode == "language":
            fields = ["accept-language"]
        else:
            fields = []
        star = path == "/star" or (path == "/switch" and mode == "star")
        payload: dict = {
            "resource": path,
            "selected": {field: headers.get(field) for field in fields},
        }
        if path == "/switch":
            payload["mode"] = mode
        if star:
            payload["sequence"] = sequence
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        etag = '"' + hashlib.sha256(body).hexdigest() + '"'
        response_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "max-age=0" if path == "/revalidate" else "max-age=600",
            "ETag": etag,
        }
        if star:
            response_headers["Vary"] = "*"
        elif fields:
            response_headers["Vary"] = ", ".join(field.title() for field in fields)

        supplied_etags = {
            token.strip()
            for line in headers.get("if-none-match", [])
            for token in line.split(",")
        }
        if not star and (etag in supplied_etags or "*" in supplied_etags):
            if path == "/revalidate":
                response_headers["Cache-Control"] = "max-age=600"
                response_headers["X-Revision"] = "refreshed"
                # No Vary on this 304: the cached selection rule is retained.
                response_headers.pop("Vary", None)
            return 304, response_headers, b""
        return 200, response_headers, body


@contextmanager
def running_origin(port: int = 0) -> Iterator[OriginFixture]:
    """Start a loopback-only origin; automatically stop it when leaving."""
    with OriginFixture(port) as origin:
        yield origin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    with running_origin(args.port) as origin:
        print(origin.base_url, flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
