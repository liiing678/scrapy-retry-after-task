"""A normal Scrapy client used by check_recovery; deliberately has no recovery code."""

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.exceptions import DropItem


def mark(path):
    Path(path).write_text("entered", encoding="utf-8")


class SQLitePipeline:
    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings["FIXTURE_DATABASE"], crawler.settings["FIXTURE_WINDOW"],
                   crawler.settings["FIXTURE_MARKER"])

    def __init__(self, database, window, marker):
        self.connection = sqlite3.connect(database)
        self.connection.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.connection.commit()
        self.window = window
        self.marker = marker

    async def process_item(self, item, spider):
        if item["id"] == "26":
            raise DropItem("synthetic terminal drop")
        if item["id"] == "27":
            raise ValueError("synthetic terminal pipeline failure")
        if item["id"] == "7" and self.window == "pipeline":
            mark(self.marker)
            await asyncio.Event().wait()
        self.connection.execute("INSERT INTO items VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
                                (item["id"], json.dumps(item, sort_keys=True)))
        self.connection.commit()
        return item

    def close_spider(self, spider):
        self.connection.close()


class FixtureSpider(scrapy.Spider):
    name = "durable_fixture"

    def __init__(self, base, resume, window, marker, **kwargs):
        super().__init__(**kwargs)
        self.base = base
        self.resume = resume
        self.window = window
        self.marker = marker

    async def start(self):
        if not self.resume:
            yield scrapy.Request(self.base + "/index", callback=self.parse_index)

    def parse_index(self, response):
        for href in response.css("a::attr(href)").getall():
            yield response.follow(href, callback=self.parse_page, errback=self.parse_error,
                                  meta={"fixture_nested": {"bytes": b"\x00\xff"}})
        # Same fingerprint, two independently accepted logical requests.
        for identity in ("post-a", "post-b"):
            yield scrapy.Request(self.base + "/echo", method="POST", body=b"\x00\xffbody",
                                  headers={"X-Test-Tag": ["one", "two"], "Cookie": "fixture=ok"},
                                  callback=self.parse_echo, dont_filter=True,
                                  cb_kwargs={"identity": identity}, priority=2,
                                  meta={"fixture_nested": {"bytes": b"\x00\xff"}})

    async def parse_page(self, response):
        assert response.meta["fixture_nested"]["bytes"] == b"\x00\xff"
        if response.url.endswith("/child/7"):
            yield {"id": "child7", "path": response.json()["path"]}
            return
        number = response.url.rsplit("/", 1)[-1]
        if number == "25":
            raise ValueError("synthetic terminal callback failure")
        if number == "7":
            yield response.follow("/child/7", callback=self.parse_page,
                                  meta={"fixture_nested": {"bytes": b"\x00\xff"}})
            if self.window == "callback":
                mark(self.marker)
                await asyncio.Event().wait()
        yield {"id": number, "path": response.json()["path"]}

    def parse_echo(self, response, identity):
        data = response.json()
        assert data["body_hex"] == b"\x00\xffbody".hex()
        assert data["tags"] == ["one", "two"]
        assert data["cookie"] == "fixture=ok"
        assert response.meta["fixture_nested"]["bytes"] == b"\x00\xff"
        yield {"id": identity, "echo": data}

    def parse_error(self, failure):
        assert failure.request.url.endswith("/missing")
        yield {"id": "missing", "status": failure.value.response.status}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--window", choices=("none", "download", "callback", "pipeline"), default="none")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--durable", action="store_true")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    state = Path(args.state)
    state.mkdir(parents=True, exist_ok=True)
    process = CrawlerProcess({
        "JOBDIR": str(state / "job"), "SCHEDULER_DURABLE": args.durable,
        "ITEM_PIPELINES": {"task.crawl_worker.SQLitePipeline": 100},
        "FIXTURE_DATABASE": str(state / "items.sqlite3"),
        "FIXTURE_WINDOW": args.window, "FIXTURE_MARKER": str(state / "entered"),
        "ROBOTSTXT_OBEY": False, "TELNETCONSOLE_ENABLED": False,
        "HTTPPROXY_ENABLED": False, "COOKIES_ENABLED": False,
        "CONCURRENT_REQUESTS": 4, "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "RETRY_TIMES": 1, "DOWNLOAD_TIMEOUT": 45,
        "LOG_LEVEL": "ERROR", "LOGSTATS_INTERVAL": 0,
        "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
    })
    crawler = process.create_crawler(FixtureSpider)
    errors = []
    deferred = process.crawl(crawler, base=args.base, resume=args.resume,
                             window=args.window, marker=str(state / "entered"))
    deferred.addErrback(lambda failure: errors.append(str(failure.value)))
    process.start()
    report = {"errors": errors, "stats": crawler.stats.get_stats()}
    Path(args.report).write_text(json.dumps(report, default=str, indent=2), encoding="utf-8")
    if errors or report["stats"].get("finish_reason") != "finished":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
