"""Acceptance examples using real HTTP and fresh Scrapy subprocesses.

The Vary-specific examples fail at the unmodified upstream starting point.
The disabled-setting /plain example is a passing environment control.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from task_materials.origin import running_origin


ROOT = Path(__file__).resolve().parents[1]
BACKENDS = [
    pytest.param("scrapy.extensions.httpcache.FilesystemCacheStorage", False, id="filesystem"),
    pytest.param("scrapy.extensions.httpcache.FilesystemCacheStorage", True, id="filesystem-gzip"),
    pytest.param("scrapy.extensions.httpcache.DbmCacheStorage", False, id="dbm-dumb"),
]


def run_probe(tmp_path, origin, storage, gzip, requests, *, vary_enabled=True):
    """Each call starts a new interpreter and reopens the same on-disk cache."""
    plan_path = tmp_path / "request-plan.json"
    result_path = tmp_path / "request-result.json"
    plan_path.write_text(
        json.dumps(
            {
                "base_url": origin.base_url,
                "cache_dir": str(tmp_path / "httpcache"),
                "storage": storage,
                "gzip": gzip,
                "vary_enabled": vary_enabled,
                "requests": requests,
            }
        ),
        encoding="utf-8",
    )
    # Remove any previous result so a failed subprocess cannot reuse it.
    result_path.unlink(missing_ok=True)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [sys.executable, "-m", "task_materials.crawl_probe", str(plan_path), str(result_path)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert result_path.is_file(), "Crawler process did not produce its response records"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert not result["errors"], result["errors"]
    assert Path(result["source"]).resolve() == (ROOT / "scrapy" / "__init__.py").resolve(), result["source"]
    assert len(result["responses"]) == len(requests), result
    return result["responses"]


@pytest.mark.parametrize(("storage", "gzip"), BACKENDS)
def test_article_variants_survive_a_new_crawler_process(tmp_path, storage, gzip):
    choices = [("en", "CN"), ("zh", "CN"), ("en", "US")]
    requests = [
        {"path": "/article", "headers": {"Accept-Language": language, "X-Region": region}}
        for language, region in choices
    ]
    with running_origin() as origin:
        first = run_probe(tmp_path, origin, storage, gzip, requests + requests)
        first_network_count = len(origin.request_log)
        reopened = run_probe(tmp_path, origin, storage, gzip, requests)

        expected = [
            {"accept-language": [language], "x-region": [region]}
            for language, region in choices
        ]
        assert [json.loads(row["body"])["selected"] for row in first] == expected + expected
        assert [json.loads(row["body"])["selected"] for row in reopened] == expected
        assert first_network_count == 3, origin.request_log
        assert len(origin.request_log) == 3, origin.request_log
        assert [row["status"] for row in origin.request_log] == [200, 200, 200]
        assert all("if-none-match" not in row["headers"] for row in origin.request_log)
        assert all(row["status"] == 200 for row in first + reopened)
        assert all("cached" not in row["flags"] for row in first[:3])
        assert all("cached" in row["flags"] for row in first[3:] + reopened)


@pytest.mark.parametrize(("storage", "gzip"), BACKENDS)
def test_304_metadata_is_persisted_and_stops_network_revalidation(tmp_path, storage, gzip):
    request = {"path": "/revalidate", "headers": {"Accept-Language": "zh", "X-Region": "CN"}}
    with running_origin() as origin:
        first = run_probe(tmp_path, origin, storage, gzip, [request] * 3)
        first_network_count = len(origin.request_log)
        reopened = run_probe(tmp_path, origin, storage, gzip, [request])

        assert first_network_count == 2, origin.request_log
        assert [row["status"] for row in origin.request_log] == [200, 304], origin.request_log
        assert origin.request_log[1]["headers"]["if-none-match"] == first[0]["headers"]["etag"]
        assert all(row["status"] == 200 for row in first + reopened)
        assert first[0]["body"]
        assert all(row["body"] == first[0]["body"] for row in first + reopened)
        assert first[0]["headers"]["cache-control"] == ["max-age=0"]
        for row in first[1:] + reopened:
            assert "cached" in row["flags"]
            assert row["headers"]["cache-control"] == ["max-age=600"]
            assert row["headers"]["x-revision"] == ["refreshed"]
            assert row["headers"]["vary"] == ["Accept-Language, X-Region"]
            assert row["headers"]["content-length"] == [str(len(row["body"].encode("utf-8")))]


@pytest.mark.parametrize(("storage", "gzip"), BACKENDS)
def test_star_is_not_reused(tmp_path, storage, gzip):
    with running_origin() as origin:
        responses = run_probe(tmp_path, origin, storage, gzip, [{"path": "/star"}] * 2)
        assert len(origin.request_log) == 2, origin.request_log
        assert all(row["status"] == 200 for row in responses)
        assert all("cached" not in row["flags"] for row in responses)
        assert responses[0]["body"] != responses[1]["body"]


@pytest.mark.parametrize(("storage", "gzip"), BACKENDS)
def test_disabled_setting_plain_cache_control(tmp_path, storage, gzip):
    """This control should already pass before implementing the new feature."""
    requests = [
        {"path": "/plain", "headers": {"X-Region": "CN"}},
        {"path": "/plain", "headers": {"X-Region": "US"}},
    ]
    with running_origin() as origin:
        first = run_probe(tmp_path, origin, storage, gzip, requests, vary_enabled=False)
        reopened = run_probe(tmp_path, origin, storage, gzip, requests, vary_enabled=False)
        assert len(origin.request_log) == 1, origin.request_log
        assert all(row["body"] == first[0]["body"] for row in first + reopened)
        assert all(row["status"] == 200 for row in first + reopened)
        assert "cached" not in first[0]["flags"]
        assert all("cached" in row["flags"] for row in first[1:] + reopened)
