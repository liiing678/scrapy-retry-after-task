"""Local HTTP fixture. No Scrapy implementation or external service is used."""

from __future__ import annotations

import argparse
import copy
import json
import threading
import time
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class RateLimitServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int = 0):
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.counts: dict[tuple[str, str], int] = {}
        super().__init__(("127.0.0.1", port), RateLimitHandler)

    def event_log(self):
        with self.lock:
            return copy.deepcopy(self.events)


class RateLimitHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string, *args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query, keep_blank_values=True)
        query = {key: entries[-1] for key, entries in values.items()}
        if parsed.path == "/control/log":
            self.respond(200, self.server.event_log(), [])
            return
        routes = {"/first_429_then_200": 429, "/first_503_then_200": 503,
                  "/first_error_then_200": 429, "/ok": 200}
        if parsed.path not in routes:
            self.respond(404, {"error": "unknown fixture route"}, [])
            return
        try:
            status = int(query.get("status", routes[parsed.path]))
            delay = float(query.get("delay_ms", "0")) / 1000
            first_count = int(query.get("first_count", "1"))
            if not 100 <= status <= 599 or not 0 <= delay <= 60 or not 0 <= first_count <= 100:
                raise ValueError("parameter out of range")
        except ValueError as exc:
            self.respond(400, {"error": str(exc)}, [])
            return
        key = query.get("key", query.get("id", parsed.path))
        with self.server.lock:
            count_key = (parsed.path, key)
            ordinal = self.server.counts.get(count_key, 0) + 1
            self.server.counts[count_key] = ordinal
            if ordinal > first_count:
                status = 200
            common = {"id": query.get("id", key), "key": key, "path": parsed.path,
                      "query": query, "ordinal": ordinal, "status": status}
            self.server.events.append({**common, "kind": "arrival", "time": time.monotonic(),
                                       "wall_time": time.time(), "retry_after": []})
        if delay:
            time.sleep(delay)
        headers = []
        if status != 200 or query.get("header_on_200") == "1":
            if "header" in values:
                headers = values["header"]
            elif query.get("header_mode", "seconds") != "none":
                value = query.get("retry_after", "2")
                if query.get("header_mode") == "date":
                    try:
                        value = formatdate(time.time() + float(value), usegmt=True)
                    except ValueError:
                        value = "not-a-date"
                headers = [value]
        if any("\r" in value or "\n" in value for value in headers):
            self.respond(400, {"error": "newline in header"}, [])
            return
        with self.server.lock:
            self.server.events.append({**common, "kind": "response", "time": time.monotonic(),
                                       "wall_time": time.time(), "retry_after": headers[:]})
        self.respond(status, {"id": common["id"], "ordinal": ordinal, "status": status}, headers)

    def respond(self, status, payload, retry_after):
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            for value in retry_after:
                self.send_header("Retry-After", value)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        self.close_connection = True


class LocalRateLimitSite:
    """Context-managed fixture with a dynamic loopback port and in-memory log."""

    def __init__(self, port=0):
        self.server = RateLimitServer(port)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def event_log(self):
        return self.server.event_log()

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def main():
    parser = argparse.ArgumentParser(description="Local Retry-After fixture")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    with LocalRateLimitSite(args.port) as site:
        print(json.dumps({"url": site.url}), flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
