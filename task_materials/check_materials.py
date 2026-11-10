"""Check fixture, repository import, and unchanged baseline retry behavior."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from acceptance import events, first_same_gap, run_probe


def main():
    for file in HERE.glob("*.py"):
        ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    import scrapy
    import twisted
    if Path(scrapy.__file__).resolve().parent != ROOT / "scrapy":
        raise RuntimeError("Scrapy import does not point to the task repository")
    data = run_probe(enabled=False)
    if data["stats"].get("retry/count", 0) != 1:
        raise AssertionError("existing RetryMiddleware did not retry the controlled 429")
    if len(events(data, "arrival", "limited")) != 2:
        raise AssertionError("controlled source request did not return 429 then 200")
    if first_same_gap(data) >= 0.65:
        raise AssertionError("baseline fixture unexpectedly paused the slot")
    if not events(data, "arrival", "other-after"):
        raise AssertionError("independent-slot followup was not exercised")
    scenarios = ("date", "invalid", "cap", "overlap", "shorter", "inflight",
                 "dont-retry", "exhausted", "missing-header")
    for scenario in scenarios:
        # Fixture checks use the disabled setting and do not test the requested
        # new behavior. Each process owns and closes its local site and reactor.
        result = run_probe(scenario, enabled=False)
        if not result["events"] or result["errors"]:
            raise AssertionError(f"unusable fixture scenario: {scenario}")
    for extra in (("--header=1", "--header=2"), ("--status", "503"),
                  ("--status", "418"), ("--autothrottle-enabled",)):
        run_probe(enabled=False, extra=extra)
    print(json.dumps({"materials": "PASS", "disabled_retry_baseline": "PASS",
                      "fixture_runs": 14,
                      "scrapy": scrapy.__version__, "twisted": twisted.__version__,
                      "target_cooldown": "NOT_VERIFIED_BY_THIS_CHECK"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
