"""Run a supplied request sequence through an actual Scrapy crawler process.

The JSON plan and result files belong in a temporary directory. This probe uses
the repository's HTTP cache; it does not implement caching or patch Scrapy.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import scrapy
from scrapy import Request, Spider
from scrapy.crawler import CrawlerProcess
from scrapy.utils.defer import maybe_deferred_to_future


def run(plan: dict) -> dict:
    result: dict = {
        "source": str(Path(scrapy.__file__).resolve()),
        "responses": [],
        "errors": [],
    }

    class ProbeSpider(Spider):
        name = "vary_http_probe"

        async def start(self):
            try:
                for entry in plan["requests"]:
                    request = Request(
                        plan["base_url"] + entry["path"],
                        headers=entry.get("headers", {}),
                        dont_filter=True,
                        meta=entry.get("meta", {}),
                    )
                    response = await maybe_deferred_to_future(
                        self.crawler.engine.download(request)
                    )
                    result["responses"].append(
                        {
                            "url": response.url,
                            "status": response.status,
                            "headers": {
                                name.decode("latin-1").lower(): [
                                    value.decode("latin-1")
                                    for value in response.headers.getlist(name)
                                ]
                                for name in response.headers
                            },
                            "body": response.body.decode("utf-8"),
                            "flags": list(response.flags),
                        }
                    )
            except Exception:
                result["errors"].append(traceback.format_exc())
            # Spider.start is an async generator even when downloads are awaited
            # directly to impose a deterministic request order.
            return
            yield

    settings = {
        "HTTPCACHE_ENABLED": True,
        "HTTPCACHE_VARY_ENABLED": plan.get("vary_enabled", True),
        "HTTPCACHE_POLICY": "scrapy.extensions.httpcache.RFC2616Policy",
        "HTTPCACHE_STORAGE": plan["storage"],
        "HTTPCACHE_DIR": plan["cache_dir"],
        "HTTPCACHE_GZIP": plan.get("gzip", False),
        "HTTPCACHE_DBM_MODULE": "dbm.dumb",
        "HTTPCACHE_EXPIRATION_SECS": 0,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_ENABLED": False,
        "HTTPPROXY_ENABLED": False,
        "ROBOTSTXT_OBEY": False,
        "COOKIES_ENABLED": False,
        "COMPRESSION_ENABLED": False,
        "RETRY_ENABLED": False,
        "DEFAULT_REQUEST_HEADERS": {},
        "DOWNLOAD_TIMEOUT": 10,
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
    }
    process = CrawlerProcess(settings, install_root_handler=False)

    def crawl_failed(failure):
        result["errors"].append(failure.getTraceback())

    process.crawl(ProbeSpider).addErrback(crawl_failed)
    process.start()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    result = run(json.loads(args.plan.read_text(encoding="utf-8")))
    args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if result["errors"]:
        print("\n".join(result["errors"]), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
