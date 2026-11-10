"""Synthetic localhost input; no external crawl targets or recovery implementation."""

from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


class LocalSite:
    def __init__(self, hold_download=False):
        self.hold_download = hold_download
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.hits = Counter()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, ConnectionAbortedError):
                    # A worker can be killed before it sends its request line.
                    pass

            def do_GET(self):
                with owner.lock:
                    owner.hits[self.path] += 1
                    count = owner.hits[self.path]
                if self.path == "/index":
                    links = [f"/page/{n}" for n in range(24)]
                    links += ["/page/3", "/page/8", "/redirect", "/missing"]
                    links += ["/page/25", "/page/26", "/page/27"]
                    body = "".join(f'<a href="{p}">{p}</a>' for p in links)
                    self.respond(200, body.encode(), "text/html")
                elif self.path == "/redirect":
                    self.respond(302, b"", headers={"Location": "/page/24"})
                elif self.path == "/missing":
                    self.respond(404, b"synthetic terminal error")
                elif self.path == "/page/0" and count == 1:
                    self.respond(503, b"synthetic transient error")
                elif self.path.startswith("/page/") or self.path == "/child/7":
                    if self.path == "/page/7" and owner.hold_download:
                        owner.entered.set()
                        if not owner.release.wait(40):
                            self.respond(500, b"fixture hold timed out")
                            return
                    self.respond(200, json.dumps({"path": self.path}).encode(), "application/json")
                else:
                    self.respond(404, b"unknown fixture path")

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                with owner.lock:
                    owner.hits[self.path] += 1
                data = {"body_hex": body.hex(), "tags": self.headers.get_all("X-Test-Tag"),
                        "cookie": self.headers.get("Cookie")}
                self.respond(200, json.dumps(data).encode(), "application/json")

            def respond(self, status, body, content_type="text/plain", headers=None):
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    for name, value in (headers or {}).items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # The controller deliberately kills a connected worker.
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def counts(self):
        with self.lock:
            return dict(self.hits)

    def resume(self):
        self.hold_download = False
        self.release.set()
