"""Public process-crash scenarios. Uses the current checkout, never patches it."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

from task.local_site import LocalSite


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {str(n) for n in range(25)} | {"child7", "missing", "post-a", "post-b"}


def rows(state):
    database = state / "items.sqlite3"
    if not database.exists():
        return {}
    with closing(sqlite3.connect(database)) as connection:
        return {key: json.loads(value) for key, value in connection.execute("SELECT id, value FROM items")}


def worker(site, state, *, durable, resume=False, window="none", label="run"):
    report = state / f"{label}.json"
    log = state / f"{label}.log"
    command = [sys.executable, "-m", "task.crawl_worker", "--base", site.url,
               "--state", str(state), "--report", str(report), "--window", window]
    if durable:
        command.append("--durable")
    if resume:
        command.append("--resume")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT)
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    return process, report, log


def finish(process, report, log):
    try:
        code = process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise AssertionError(f"worker did not terminate; tail: {log.read_text(encoding='utf-8')[-5000:]}")
    if code != 0:
        raise AssertionError(f"worker exit={code}; tail: {log.read_text(encoding='utf-8')[-5000:]}")
    return json.loads(report.read_text(encoding="utf-8"))


def await_window(process, site, state, window, log):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        entered = site.entered.is_set() if window == "download" else (state / "entered").exists()
        if entered:
            return
        if process.poll() is not None:
            raise AssertionError(f"worker ended before {window}: {log.read_text(encoding='utf-8')[-5000:]}")
        time.sleep(0.02)  # Poll a positive barrier signal, never guess a crash delay.
    raise AssertionError(f"fixture did not reach {window} barrier")


def validate_content(actual):
    assert set(actual) == EXPECTED, f"missing={sorted(EXPECTED - set(actual))}, unexpected={sorted(set(actual) - EXPECTED)}"
    for number in range(25):
        assert actual[str(number)]["path"] == f"/page/{number}"
    assert actual["child7"]["path"] == "/child/7"
    assert actual["missing"]["status"] == 404
    for identity in ("post-a", "post-b"):
        assert actual[identity]["echo"] == {"body_hex": "00ff626f6479", "tags": ["one", "two"], "cookie": "fixture=ok"}


def run_case(mode, window):
    durable = mode == "acceptance"
    with tempfile.TemporaryDirectory(prefix="scrapy-durable-") as directory:
        state = Path(directory)
        with LocalSite(hold_download=window == "download") as site:
            process = None
            try:
                if mode == "baseline":
                    process, report, log = worker(site, state, durable=False)
                    result = finish(process, report, log)
                    validate_content(rows(state))
                    hits = site.counts()
                    assert hits["/page/0"] == 2 and hits["/redirect"] == 1
                    assert hits["/page/3"] == 1 and hits["/page/8"] == 1 and hits["/echo"] == 2
                    return {"mode": mode, "items": len(rows(state)), "requests": sum(hits.values()),
                            "finish_reason": result["stats"]["finish_reason"]}
                process, report, log = worker(site, state, durable=durable, window=window, label="interrupted")
                await_window(process, site, state, window, log)
                process.kill()  # Only this controller's own child process.
                process.wait(timeout=10)
                site.resume()
                before = sorted(rows(state))
                process, report, log = worker(site, state, durable=durable, resume=True, label="recovered")
                recovered = finish(process, report, log)
                actual = rows(state)
                missing = sorted(EXPECTED - set(actual))
                if mode == "acceptance":
                    validate_content(actual)
                    assert site.counts().get("/page/7", 0) >= 2, "unconfirmed page was not redelivered"
                    assert recovered["stats"].get("scheduler/durable/recovered", 0) >= 1
                    assert recovered["stats"].get("scheduler/durable/acked", 0) >= 1
                    assert recovered["stats"].get("scheduler/durable/pending") == 0
                    completed_hits = site.counts()
                    process, report, log = worker(site, state, durable=True, resume=True, label="second_restart")
                    empty_restart = finish(process, report, log)
                    assert empty_restart["stats"].get("scheduler/durable/recovered", 0) == 0
                    assert empty_restart["stats"].get("scheduler/durable/acked", 0) == 0
                    assert empty_restart["stats"].get("scheduler/durable/pending") == 0
                    assert site.counts() == completed_hits, "a completed request was replayed after a second restart"
                    validate_content(rows(state))
                else:
                    assert "7" in missing, "expected upstream's unconfirmed /page/7 to be absent after restart"
                return {"mode": mode, "window": window, "items_before_crash": len(before),
                        "items_after_restart": len(actual), "missing": missing, "hits": site.counts()}
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "reproduce", "acceptance"), default="baseline")
    parser.add_argument("--window", choices=("all", "download", "callback", "pipeline"), default="all")
    args = parser.parse_args()
    windows = ["none"] if args.mode == "baseline" else (
        ["download", "callback", "pipeline"] if args.window == "all" else [args.window])
    for window in windows:
        print(json.dumps(run_case(args.mode, window), ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
