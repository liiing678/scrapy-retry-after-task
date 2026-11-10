"""Public behavioral checks for the Vary cache extension.

These tests intentionally fail against the unmodified upstream baseline. They
exercise existing public storage and middleware entry points, not an index layout.
"""

from contextlib import contextmanager
from email.utils import formatdate
from hashlib import sha256
from time import sleep, time

import pytest

from scrapy.downloadermiddlewares.httpcache import HttpCacheMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Headers, Request, Response
from scrapy.spiders import Spider
from scrapy.utils.request import fingerprint
from scrapy.utils.test import get_crawler
from task_materials.legacy_seed import seed_legacy


STORAGES = [
    pytest.param(("FilesystemCacheStorage", False), id="filesystem"),
    pytest.param(("FilesystemCacheStorage", True), id="filesystem-gzip"),
    pytest.param(("DbmCacheStorage", False), id="dbm-dumb"),
]
POLICIES = ["RFC2616Policy", "DummyPolicy"]
URL = "http://cache.example.test/article"


class TenantFingerprinter:
    """An existing application's independent request identity policy."""

    def fingerprint(self, request):
        tenant = request.headers.get(b"X-Tenant", b"")
        return sha256(fingerprint(request) + b"\x00" + tenant).digest()


@pytest.fixture(params=STORAGES)
def cache(request, tmp_path):
    storage, gzip = request.param

    @contextmanager
    def opened(*, spider_name="vary-check", enabled=True, policy="RFC2616Policy", **extra):
        settings = {
            "HTTPCACHE_ENABLED": True,
            "HTTPCACHE_DIR": str(tmp_path / "cache"),
            "HTTPCACHE_STORAGE": f"scrapy.extensions.httpcache.{storage}",
            "HTTPCACHE_POLICY": f"scrapy.extensions.httpcache.{policy}",
            "HTTPCACHE_DBM_MODULE": "dbm.dumb",
            "HTTPCACHE_GZIP": gzip,
            "HTTPCACHE_EXPIRATION_SECS": 0,
        }
        if enabled is not None:
            settings["HTTPCACHE_VARY_ENABLED"] = enabled
        settings.update(extra)
        crawler = get_crawler(Spider, settings)
        spider = crawler._create_spider(spider_name)
        middleware = HttpCacheMiddleware.from_crawler(crawler)
        middleware.spider_opened(spider)
        try:
            yield middleware, spider
        finally:
            middleware.spider_closed(spider)

    return opened


def req(language=b"en", *, region=b"east", url=URL, **kwargs):
    headers = Headers({b"X-Region": region})
    if language is not None:
        headers[b"Accept-Language"] = language
    headers.update(kwargs.pop("headers", {}))
    return Request(url, headers=headers, **kwargs)


def response(request, body, *, vary=b"Accept-Language", fresh=True, etag=None, **extra):
    headers = Headers(
        {
            b"Content-Type": b"application/octet-stream",
            b"Content-Length": str(len(body)).encode(),
            b"Cache-Control": b"max-age=600" if fresh else b"max-age=0",
            b"Date": formatdate(usegmt=True).encode(),
        }
    )
    if vary is not None:
        headers[b"Vary"] = vary
    if etag is not None:
        headers[b"ETag"] = etag
    headers.update(extra)
    return Response(request.url, body=body, headers=headers)


def save(middleware, spider, request, body, **kwargs):
    return middleware.process_response(request, response(request, body, **kwargs), spider)


def hit(middleware, spider, request, body):
    result = middleware.process_request(request, spider)
    assert isinstance(result, Response), f"expected cached response for {dict(request.headers)!r}"
    assert result.body == body
    assert "cached" in result.flags
    return result


def test_variants_coexist_and_survive_storage_reopening(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
        save(mw, spider, req(b"fr"), b"french")
        hit(mw, spider, req(b"en"), b"english")
        hit(mw, spider, req(b"fr"), b"french")
    with cache() as (mw, spider):
        hit(mw, spider, req(b"fr"), b"french")
        hit(mw, spider, req(b"en"), b"english")


def test_selector_values_preserve_missing_empty_order_and_internal_bytes(cache):
    # Each pair represents two different variants; strip only outer SP and HTAB.
    pairs = [
        (None, b""),
        (b"en", b"EN"),
        ([b"en", b"fr"], [b"fr", b"en"]),
        ([b"en", b"fr"], b"en,fr"),
        (b"en  fr", b"en fr"),
    ]
    with cache() as (mw, spider):
        for index, (left, right) in enumerate(pairs):
            url = f"{URL}?pair={index}"
            save(mw, spider, req(left, url=url), b"left")
            save(mw, spider, req(right, url=url), b"right")
            hit(mw, spider, req(left, url=url), b"left")
            hit(mw, spider, req(right, url=url), b"right")
        save(mw, spider, req(b" \ten\t "), b"trimmed")
        hit(mw, spider, req(b"en", headers={b"X-Unrelated": b"different"}), b"trimmed")


def test_vary_names_normalize_across_lines_order_case_and_duplicates(cache):
    with cache() as (mw, spider):
        first = response(
            req(b"en"), b"english", vary=[b" Accept-Language , X-Region, ", b"accept-language"]
        )
        mw.process_response(req(b"en"), first, spider)
        save(mw, spider, req(b"fr"), b"french", vary=b"x-region,ACCEPT-LANGUAGE")
        hit(mw, spider, req(b"en"), b"english")
        hit(mw, spider, req(b"fr"), b"french")
        assert mw.process_request(req(b"en", region=b"west"), spider) is None


def test_new_vary_name_set_replaces_entire_previous_group(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
        save(mw, spider, req(b"fr"), b"french")
        save(mw, spider, req(b"en", region=b"east"), b"east", vary=b"X-Region")
        hit(mw, spider, req(b"fr", region=b"east"), b"east")
        assert mw.process_request(req(b"fr", region=b"west"), spider) is None
        save(mw, spider, req(b"fr"), b"universal", vary=None)
        hit(mw, spider, req(b"de", region=b"west"), b"universal")
        save(mw, spider, req(b"fr"), b"french-again", vary=b"Accept-Language")
        assert mw.process_request(req(b"en"), spider) is None


@pytest.mark.parametrize("policy", POLICIES)
def test_star_on_any_vary_line_is_uncacheable_and_invalidates_old_group(cache, policy):
    with cache(policy=policy) as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
        save(mw, spider, req(b"fr"), b"french")
        save(mw, spider, req(b"de"), b"one-off", vary=[b"Accept-Language", b" X-Region, * "])
        for language in (b"en", b"fr", b"de"):
            assert mw.storage.retrieve_response(spider, req(language)) is None
        save(mw, spider, req(b"en"), b"new english")
        hit(mw, spider, req(b"en"), b"new english")
        assert mw.process_request(req(b"fr"), spider) is None


def test_request_method_body_and_spider_remain_independent(cache):
    requests = [req(), req(method="POST", body=b"one"), req(method="POST", body=b"two")]
    with cache() as (mw, spider):
        for index, request in enumerate(requests):
            save(mw, spider, request, str(index).encode())
        for index, request in enumerate(requests):
            hit(mw, spider, request.copy(), str(index).encode())
    with cache(spider_name="another-spider") as (mw, spider):
        for request in requests:
            assert mw.storage.retrieve_response(spider, request) is None


def test_custom_fingerprinter_is_respected_without_changing_global_identity(cache):
    with cache(REQUEST_FINGERPRINTER_CLASS=TenantFingerprinter) as (mw, spider):
        for tenant, language, body in [(b"a", b"en", b"a-en"), (b"a", b"fr", b"a-fr"), (b"b", b"en", b"b-en")]:
            save(mw, spider, req(language, headers={b"X-Tenant": tenant}), body)
        hit(mw, spider, req(b"en", headers={b"X-Tenant": b"a"}), b"a-en")
        hit(mw, spider, req(b"fr", headers={b"X-Tenant": b"a"}), b"a-fr")
        hit(mw, spider, req(b"en", headers={b"X-Tenant": b"b"}), b"b-en")
        assert fingerprint(req(b"en")) == fingerprint(req(b"fr"))
    with cache() as (mw, spider):
        assert spider.crawler.request_fingerprinter.fingerprint(req(b"en")) == spider.crawler.request_fingerprinter.fingerprint(req(b"fr"))


def test_revalidation_selects_correct_validator_merges_headers_and_persists(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english", fresh=False, etag=b'"en-v1"')
        save(mw, spider, req(b"fr"), b"french", fresh=False, etag=b'"fr-v1"')
        request = req(b"en")
        assert mw.process_request(request, spider) is None
        assert request.headers[b"If-None-Match"] == b'"en-v1"'
        updated_date = formatdate(usegmt=True).encode()
        network = Response(
            request.url,
            status=304,
            headers={
                b"Date": updated_date,
                b"Cache-Control": b"max-age=600",
                b"ETag": b'"en-v2"',
                b"X-Revision": b"refreshed",
                b"Connection": b"X-Hop",
                b"X-Hop": b"not-end-to-end",
                b"Keep-Alive": b"timeout=5",
                b"Content-Length": b"0",
            },
        )
        result = mw.process_response(request, network, spider)
        assert result.status == 200
        assert result.body == b"english"
        assert result.headers[b"Date"] == updated_date
        assert result.headers[b"Cache-Control"] == b"max-age=600"
        assert result.headers[b"ETag"] == b'"en-v2"'
        assert result.headers[b"X-Revision"] == b"refreshed"
        assert result.headers[b"Vary"] == b"Accept-Language"
        assert result.headers[b"Content-Length"] == b"7"
        for header in (b"Connection", b"X-Hop", b"Keep-Alive"):
            assert header not in result.headers
    with cache() as (mw, spider):
        result = hit(mw, spider, req(b"en"), b"english")
        assert result.headers[b"X-Revision"] == b"refreshed"
        assert result.headers[b"ETag"] == b'"en-v2"'
        french = req(b"fr")
        assert mw.process_request(french, spider) is None
        assert french.headers[b"If-None-Match"] == b'"fr-v1"'


def test_304_explicit_vary_change_reindexes_selected_body_and_removes_siblings(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english", fresh=False, etag=b'"en"')
        save(mw, spider, req(b"fr"), b"french", fresh=False, etag=b'"fr"')
        selected = req(b"en", region=b"east")
        assert mw.process_request(selected, spider) is None
        network = Response(
            selected.url,
            status=304,
            headers={b"Vary": b"X-Region", b"Cache-Control": b"max-age=600"},
        )
        result = mw.process_response(selected, network, spider)
        assert result.body == b"english"
        assert result.headers[b"Vary"] == b"X-Region"
    with cache() as (mw, spider):
        hit(mw, spider, req(b"fr", region=b"east"), b"english")
        assert mw.process_request(req(b"fr", region=b"west"), spider) is None


def test_304_star_returns_current_body_but_clears_future_cache_hits(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english", fresh=False, etag=b'"en"')
        save(mw, spider, req(b"fr"), b"french", fresh=False, etag=b'"fr"')
        selected = req(b"en")
        assert mw.process_request(selected, spider) is None
        result = mw.process_response(selected, Response(selected.url, status=304, headers={b"Vary": b"*"}), spider)
        assert result.status == 200
        assert result.body == b"english"
        assert mw.storage.retrieve_response(spider, req(b"en")) is None
        assert mw.storage.retrieve_response(spider, req(b"fr")) is None
    with cache() as (mw, spider):
        assert mw.process_request(req(b"en"), spider) is None


def test_variant_miss_has_no_foreign_validator_or_error_recovery(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english", fresh=False, etag=b'"en"')
        missing = req(b"fr")
        assert mw.process_request(missing, spider) is None
        assert b"If-None-Match" not in missing.headers
        assert b"If-Modified-Since" not in missing.headers
        assert mw.process_exception(missing, OSError("synthetic source failure"), spider) is None
        matched = req(b"en")
        assert mw.process_request(matched, spider) is None
        recovered = mw.process_exception(matched, OSError("synthetic source failure"), spider)
        assert recovered.body == b"english"


def test_ignore_missing_applies_to_missing_variant(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
    with cache(HTTPCACHE_IGNORE_MISSING=True) as (mw, spider):
        hit(mw, spider, req(b"en"), b"english")
        with pytest.raises(IgnoreRequest):
            mw.process_request(req(b"fr"), spider)


def test_dont_cache_bypasses_reads_writes_and_group_invalidation(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
        bypass = req(b"en", meta={"dont_cache": True})
        assert mw.process_request(bypass, spider) is None
        result = save(mw, spider, bypass, b"uncached", vary=b"*")
        assert result.body == b"uncached"
        hit(mw, spider, req(b"en"), b"english")


def test_expiration_still_applies_to_variants(cache, monkeypatch):
    with cache(HTTPCACHE_EXPIRATION_SECS=1) as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
        save(mw, spider, req(b"fr"), b"french")
        future = time() + 30
        monkeypatch.setattr("scrapy.extensions.httpcache.time", lambda: future)
        assert mw.storage.retrieve_response(spider, req(b"en")) is None
        assert mw.storage.retrieve_response(spider, req(b"fr")) is None


@pytest.mark.parametrize("enabled", [False, None], ids=["explicitly-disabled", "default"])
def test_disabled_setting_preserves_upstream_single_entry_behavior(cache, enabled):
    with cache(enabled=enabled) as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
        hit(mw, spider, req(b"fr"), b"english")
        save(mw, spider, req(b"fr"), b"french")
        hit(mw, spider, req(b"en"), b"french")


def test_saving_sibling_does_not_refresh_expired_variant(cache):
    with cache(HTTPCACHE_EXPIRATION_SECS=1) as (mw, spider):
        save(mw, spider, req(b"en"), b"english")
        sleep(1.2)
        save(mw, spider, req(b"fr"), b"french")
        assert mw.storage.retrieve_response(spider, req(b"en")) is None
        hit(mw, spider, req(b"fr"), b"french")


def test_star_beats_always_store_without_affecting_another_base_key(cache):
    with cache(HTTPCACHE_ALWAYS_STORE=True) as (mw, spider):
        other = req(url=URL + "?other=1")
        save(mw, spider, other, b"other")
        save(mw, spider, req(), b"english")
        save(mw, spider, req(), b"private", vary=b"*")
        assert mw.storage.retrieve_response(spider, req()) is None
        hit(mw, spider, other, b"other")


def test_rfc_no_store_is_not_persisted(cache):
    with cache() as (mw, spider):
        request = req()
        network = response(request, b"transient")
        network.headers[b"Cache-Control"] = b"no-store"
        result = mw.process_response(request, network, spider)
        assert result.body == b"transient"
        assert mw.storage.retrieve_response(spider, request) is None


@pytest.mark.parametrize("vary", [None, b"Accept-Language"], ids=["plain", "varied"])
def test_old_format_is_readable_or_safely_missed(cache, vary):
    request = req()
    with cache() as (mw, spider):
        settings = spider.crawler.settings
        root = settings["HTTPCACHE_DIR"]
        kind = "dbm" if settings["HTTPCACHE_STORAGE"].endswith("DbmCacheStorage") else "filesystem"
        gzip = settings.getbool("HTTPCACHE_GZIP")
        name = spider.name
        key = spider.crawler.request_fingerprinter.fingerprint(request)
    seed_legacy(kind, root, name, key, request, response(request, b"legacy", vary=vary), gzip)
    with cache() as (mw, spider):
        if vary is None:
            hit(mw, spider, request, b"legacy")
        else:
            assert mw.storage.retrieve_response(spider, request) is None


def test_304_explicit_empty_vary_becomes_universal(cache):
    with cache() as (mw, spider):
        save(mw, spider, req(), b"english", fresh=False, etag=b'"en"')
        selected = req()
        assert mw.process_request(selected, spider) is None
        result = mw.process_response(selected, Response(
            selected.url, status=304,
            headers={b"Vary": b"", b"Cache-Control": b"max-age=600"},
        ), spider)
        assert result.body == b"english"
        hit(mw, spider, req(b"fr", region=b"west"), b"english")
