"""Exercise the checked-out Scrapy code against the local rate-limit fixture.

This probe supplies inputs and records behavior. It does not implement cooling.
Run each invocation in a fresh process because Twisted reactors cannot restart.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scrapy
from scrapy import signals
from scrapy.crawler import CrawlerProcess

from rate_limit_site import LocalRateLimitSite


def boolean(value):
    if value.lower() in {"true", "1", "yes"}:
        return True
    if value.lower() in {"false", "0", "no"}:
        return False
    raise argparse.ArgumentTypeError("use true or false")


def parser():
    result = argparse.ArgumentParser(description="Local downloader timing probe")
    enable = result.add_mutually_exclusive_group()
    enable.add_argument("--enabled", action="store_true")
    enable.add_argument("--disabled", action="store_true")
    result.add_argument("--scenario", choices=["basic", "date", "invalid", "cap", "overlap", "shorter",
                                              "inflight", "dont-retry", "exhausted", "missing-header"], default="basic")
    result.add_argument("--header", action="append", help="raw Retry-After value; repeat for multiple headers")
    result.add_argument("--retry-enabled", type=boolean, default=True)
    result.add_argument("--retry-times", type=int, default=1)
    result.add_argument("--retry-after-max-delay", type=float, default=120.0)
    result.add_argument("--download-delay", type=float, default=0.0)
    result.add_argument("--autothrottle-enabled", action="store_true")
    result.add_argument("--status", type=int, default=429)
    result.add_argument("--retry-http-codes", default="500,502,503,504,522,524,408,429")
    return result


def main():
    args = parser().parse_args()
    items, errors, lifecycle, slot_samples = [], [], [], []
    started = time.monotonic()
    concurrent = 2 if args.scenario in {"overlap", "shorter", "inflight"} else 1
    settings = {
        "RETRY_AFTER_ENABLED": args.enabled,
        "RETRY_AFTER_MAX_DELAY": args.retry_after_max_delay,
        "RETRY_ENABLED": args.retry_enabled,
        "RETRY_TIMES": 0 if args.scenario == "exhausted" else args.retry_times,
        "RETRY_HTTP_CODES": [int(value) for value in args.retry_http_codes.split(",")],
        "CONCURRENT_REQUESTS": 16,
        "CONCURRENT_REQUESTS_PER_DOMAIN": concurrent,
        "DOWNLOAD_SLOTS": {"limited": {"concurrency": concurrent}, "other": {"concurrency": 1}},
        "DOWNLOAD_DELAY": args.download_delay,
        "RANDOMIZE_DOWNLOAD_DELAY": False,
        "AUTOTHROTTLE_ENABLED": args.autothrottle_enabled,
        "AUTOTHROTTLE_START_DELAY": args.download_delay,
        "AUTOTHROTTLE_MAX_DELAY": 3.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 2.0,
        "HTTPPROXY_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_ENABLED": False,
        "DOWNLOAD_TIMEOUT": 8,
        "CLOSESPIDER_TIMEOUT": 30,
    }
    with LocalRateLimitSite() as site:
        class ProbeSpider(scrapy.Spider):
            name = "retry_after_local_probe"

            def make_request(self, identifier, path="/ok", slot="limited", delay_ms=0,
                             retry_after="1", header_mode="seconds", custom_headers=False):
                query = {"id": identifier, "key": identifier, "slot": slot,
                         "delay_ms": str(delay_ms), "retry_after": retry_after, "header_mode": header_mode}
                if path != "/ok":
                    query["status"] = str(args.status)
                pairs = list(query.items())
                if custom_headers and args.header is not None:
                    pairs.extend(("header", value) for value in args.header)
                meta = {"download_slot": slot, "handle_httpstatus_all": True}
                if args.scenario == "dont-retry" and path != "/ok":
                    meta["dont_retry"] = True
                return scrapy.Request(site.url + path + "?" + urlencode(pairs),
                                      callback=self.parse, errback=self.failed,
                                      meta=meta, dont_filter=True)

            async def start(self):
                path = "/first_429_then_200" if args.status == 429 else "/first_503_then_200" if args.status == 503 else "/first_error_then_200"
                if args.scenario in {"overlap", "shorter"}:
                    yield self.make_request("limited-a", path, delay_ms=100, retry_after="1", custom_headers=True)
                    yield self.make_request("limited-b", path, delay_ms=350,
                                            retry_after="2" if args.scenario == "overlap" else "0")
                else:
                    value = "not-a-delay" if args.scenario == "invalid" else "999" if args.scenario == "cap" else "2" if args.scenario == "date" else "1"
                    mode = "date" if args.scenario == "date" else "none" if args.scenario == "missing-header" else "seconds"
                    yield self.make_request("limited", path, delay_ms=150,
                                            retry_after=value, header_mode=mode, custom_headers=True)
                    if args.scenario == "inflight":
                        yield self.make_request("inflight", delay_ms=350)
                yield self.make_request("same-1")
                yield self.make_request("same-2")
                yield self.make_request("other-hold", slot="other", delay_ms=350)

            def parse(self, response):
                payload = json.loads(response.text)
                items.append({**payload, "time": time.monotonic(), "slot": response.meta.get("download_slot"),
                              "retry_times": response.meta.get("retry_times", 0)})
                if payload["id"] == "other-hold":
                    yield self.make_request("other-after", slot="other")

            def failed(self, failure):
                errors.append({"url": failure.request.url, "error": str(failure.value), "time": time.monotonic()})

        process = CrawlerProcess(settings=settings)
        crawler = process.create_crawler(ProbeSpider)

        def on_closed(spider, reason):
            lifecycle.append({"event": "spider_closed", "reason": reason, "time": time.monotonic()})

        def on_left(request, spider):
            key = request.meta.get("download_slot")
            slot = crawler.engine.downloader.slots.get(key)
            slot_samples.append({"time": time.monotonic(), "slot": key, "url": request.url,
                                 "delay": slot.delay if slot is not None else None})

        crawler.signals.connect(on_closed, signal=signals.spider_closed, weak=False)
        crawler.signals.connect(on_left, signal=signals.request_left_downloader, weak=False)
        deferred = process.crawl(crawler)

        def on_failure(failure):
            errors.append({"error": str(failure.value), "time": time.monotonic()})
            return None

        deferred.addErrback(on_failure)
        process.start()
        from twisted.internet import reactor

        delayed = [repr(call) for call in reactor.getDelayedCalls() if call.active()]
        stats = crawler.stats.get_stats() if crawler.stats else {}
        output = {"enabled": args.enabled, "scenario": args.scenario, "base_url": site.url,
                  "scrapy_version": scrapy.__version__, "scrapy_path": scrapy.__file__,
                  "events": site.event_log(), "items": items, "errors": errors, "stats": stats,
                  "lifecycle": lifecycle, "slot_samples": slot_samples,
                  "finish_reason": stats.get("finish_reason"),
                  "duration": time.monotonic() - started, "active_delayed_calls": delayed,
                  "settings": {key: settings[key] for key in ("RETRY_AFTER_ENABLED", "RETRY_AFTER_MAX_DELAY", "RETRY_ENABLED", "RETRY_TIMES", "DOWNLOAD_DELAY")}}
    print(json.dumps(output, ensure_ascii=True, default=str))
    return 0 if output["finish_reason"] == "finished" and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
