"""Public end-to-end checks for Retry-After download-slot cooldown.

These checks exercise a local HTTP site through the repository's Scrapy code.
The upstream starting version is expected to fail the new-behavior cases.
"""

from __future__ import annotations

import argparse
import email.utils
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "task_materials" / "probe_crawl.py"
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")


def run_probe(scenario="basic", *, enabled=True, extra=(), success=True):
    command = [sys.executable, "-B", str(PROBE),
               "--enabled" if enabled else "--disabled", "--scenario", scenario, *extra]
    result = subprocess.run(command, cwd=ROOT, env=ENV, text=True,
                            encoding="utf-8", capture_output=True, timeout=35)
    if not success:
        if result.returncode == 0:
            raise AssertionError("invalid configuration was accepted")
        return result
    if result.returncode != 0:
        raise AssertionError(f"probe exited {result.returncode}: {result.stderr[-3000:]}")
    try:
        data = json.loads(result.stdout)
    except ValueError as error:
        raise AssertionError(f"probe did not return JSON: {result.stdout[-1500:]}") from error
    if data.get("finish_reason") != "finished":
        raise AssertionError(f"unexpected finish reason: {data.get('finish_reason')}")
    if data.get("active_delayed_calls"):
        raise AssertionError(f"active timers remain after normal shutdown: {data['active_delayed_calls']}")
    return data


def events(data, kind, identifier=None):
    return [event for event in data["events"]
            if event["kind"] == kind and (identifier is None or event["id"] == identifier)]


def first_event(data, kind, identifier):
    found = events(data, kind, identifier)
    if not found:
        raise AssertionError(f"missing {kind} event for {identifier}")
    return found[0]


def first_same_gap(data):
    limited = first_event(data, "response", "limited")
    same = first_event(data, "arrival", "same-1")
    return same["time"] - limited["time"]


def assert_paused(data, seconds, *, slack=0.08):
    response = first_event(data, "response", "limited")
    for identifier in ("same-1", "same-2"):
        for arrival in events(data, "arrival", identifier):
            gap = arrival["time"] - response["time"]
            if gap < seconds - slack:
                raise AssertionError(f"{identifier} started after {gap:.3f}s; cooldown requires {seconds:.3f}s")
    for arrival in events(data, "arrival", "limited")[1:]:
        gap = arrival["time"] - response["time"]
        if gap < seconds - slack:
            raise AssertionError(f"retry started after {gap:.3f}s; cooldown requires {seconds:.3f}s")


class Acceptance(unittest.TestCase):
    def test_settings_defaults(self):
        sys.path.insert(0, str(ROOT))
        from scrapy.settings import default_settings
        self.assertIs(getattr(default_settings, "RETRY_AFTER_ENABLED", None), False)
        self.assertEqual(getattr(default_settings, "RETRY_AFTER_MAX_DELAY", None), 120)

    def test_slot_pause_and_other_slot_progress(self):
        data = run_probe()
        assert_paused(data, 1)
        limited = first_event(data, "response", "limited")
        other = first_event(data, "arrival", "other-after")
        self.assertGreater(other["time"], limited["time"])
        self.assertLess(other["time"] - limited["time"], 0.8,
                        "an independent download slot was blocked")
        self.assertEqual(data["stats"].get("retry/count", 0), 1)
        self.assertEqual(len(events(data, "arrival", "limited")), 2)

    def test_disabled_preserves_existing_retry(self):
        data = run_probe(enabled=False)
        self.assertLess(first_same_gap(data), 0.65)
        self.assertEqual(data["stats"].get("retry/count", 0), 1)

    def test_retry_disabled_and_dont_retry(self):
        for scenario, extra in (("basic", ("--retry-enabled", "false")), ("dont-retry", ())):
            with self.subTest(scenario=scenario):
                data = run_probe(scenario, extra=extra)
                self.assertLess(first_same_gap(data), 0.65)
                self.assertEqual(data["stats"].get("retry/count", 0), 0)
                self.assertEqual(len(events(data, "arrival", "limited")), 1)

    def test_exhausted_budget_still_cools_slot(self):
        data = run_probe("exhausted")
        assert_paused(data, 1)
        self.assertEqual(data["stats"].get("retry/count", 0), 0)
        self.assertEqual(len(events(data, "arrival", "limited")), 1)

    def test_status_filter_and_missing_header(self):
        data = run_probe(extra=("--status", "418"))
        self.assertLess(first_same_gap(data), 0.65)
        self.assertEqual(data["stats"].get("retry/count", 0), 0)
        data = run_probe(extra=("--status", "418", "--retry-http-codes", "418"))
        assert_paused(data, 1)
        self.assertEqual(data["stats"].get("retry/count", 0), 1)
        data = run_probe(extra=("--status", "503"))
        assert_paused(data, 1)
        self.assertEqual(data["stats"].get("retry/count", 0), 1)
        data = run_probe("missing-header")
        self.assertLess(first_same_gap(data), 0.65)
        self.assertEqual(data["stats"].get("retry/count", 0), 1)

    def test_header_ows_and_integer(self):
        data = run_probe(extra=("--header= \t1 \t",))
        assert_paused(data, 1)

    def test_http_date(self):
        data = run_probe("date")
        response = first_event(data, "response", "limited")
        header = response["retry_after"][0]
        target = email.utils.parsedate_to_datetime(header).timestamp()
        remaining = max(0, target - response["wall_time"])
        self.assertGreater(remaining, 0.7, "date fixture did not produce a future deadline")
        assert_paused(data, remaining, slack=0.12)

    def test_invalid_headers_and_past_date(self):
        values = ("-1", "1.2", "NaN", "tomorrow", "1,2", "+1", "1e2", "",
                  "Wed, 21 Oct 2015 07:28:00 GMT")
        for value in values:
            with self.subTest(header=value):
                data = run_probe(extra=(f"--header={value}",))
                self.assertLess(first_same_gap(data), 0.65)
                self.assertEqual(data["stats"].get("retry/count", 0), 1)
        data = run_probe(extra=("--header=1", "--header=2"))
        self.assertLess(first_same_gap(data), 0.65, "multiple header values must not create a cooldown")

    def test_max_delay_and_zero_limit(self):
        data = run_probe("cap", extra=("--retry-after-max-delay", "0.4"))
        assert_paused(data, 0.4)
        self.assertLess(first_same_gap(data), 0.95, "large header was not capped")
        data = run_probe("cap", extra=("--retry-after-max-delay", "0"))
        self.assertLess(first_same_gap(data), 0.65)
        data = run_probe(extra=("--header=" + "9" * 1000,
                                "--retry-after-max-delay", "0.4"))
        assert_paused(data, 0.4)
        self.assertLess(first_same_gap(data), 0.95, "large integer header was not safely capped")

    def test_invalid_max_delay_configuration(self):
        for value in ("-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                run_probe(extra=(f"--retry-after-max-delay={value}",), success=False)

    def test_overlapping_limits_take_max_deadline(self):
        for scenario in ("overlap", "shorter"):
            with self.subTest(scenario=scenario):
                data = run_probe(scenario)
                limits = [first_event(data, "response", identifier)
                          for identifier in ("limited-a", "limited-b")]
                deadlines = [event["time"] + float(event["retry_after"][0]) for event in limits]
                expected = max(deadlines)
                arrivals = [first_event(data, "arrival", identifier)
                            for identifier in ("same-1", "same-2")]
                self.assertGreaterEqual(min(event["time"] for event in arrivals), expected - 0.08)
                self.assertLess(min(event["time"] for event in arrivals), expected + 0.65,
                                "overlapping waits were added instead of taking their maximum")
                self.assertEqual(data["stats"].get("retry/count", 0), 2)

    def test_inflight_transfer_is_not_cancelled(self):
        data = run_probe("inflight")
        limited = first_event(data, "response", "limited")
        arrival = first_event(data, "arrival", "inflight")
        response = first_event(data, "response", "inflight")
        self.assertLess(arrival["time"], limited["time"])
        self.assertGreater(response["time"], limited["time"])
        self.assertLess(response["time"] - limited["time"], 0.8)
        self.assertEqual(response["status"], 200)
        self.assertEqual(len(events(data, "arrival", "inflight")), 1)
        assert_paused(data, 1)

    def test_download_delay_uses_max_not_sum(self):
        data = run_probe(extra=("--download-delay", "1"))
        assert_paused(data, 1)
        gap = first_same_gap(data)
        self.assertLess(gap, 1.55, "DOWNLOAD_DELAY and cooldown appear to have been added")
        one = first_event(data, "arrival", "same-1")
        two = first_event(data, "arrival", "same-2")
        self.assertGreaterEqual(two["time"] - one["time"], 0.9,
                                "existing inter-request DOWNLOAD_DELAY was overwritten")

    def test_autothrottle_delay_is_not_replaced_by_cooldown(self):
        data = run_probe(extra=("--autothrottle-enabled",))
        assert_paused(data, 1)
        samples = [sample for sample in data["slot_samples"]
                   if sample["slot"] == "limited" and sample["delay"] is not None]
        self.assertTrue(samples, "probe did not capture AutoThrottle's effective slot delay")
        # The synthetic 429 response takes 150 ms; target concurrency is 2.
        # AutoThrottle may vary with measured network latency, but a one-second
        # server cooldown must not become this slot's permanent base delay.
        self.assertLess(max(sample["delay"] for sample in samples), 0.65,
                        "server cooldown overwrote the adaptive base delay")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--case", help="run one listed case")
    args = parser.parse_args()
    loader = unittest.TestLoader()
    names = loader.getTestCaseNames(Acceptance)
    if args.list:
        print("\n".join(names))
        return 0
    if args.case and args.case not in names:
        parser.error(f"unknown case: {args.case}")
    suite = loader.loadTestsFromName(args.case, Acceptance) if args.case else loader.loadTestsFromTestCase(Acceptance)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
