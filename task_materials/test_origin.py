"""Checks of the synthetic origin itself, independent of Scrapy's cache."""

from __future__ import annotations

import http.client
import json
import unittest
from urllib.parse import urlsplit

from task_materials.origin import OriginFixture


class OriginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.origin = OriginFixture().start()
        self.addCleanup(self.origin.close)

    def get(self, path: str, headers: list[tuple[str, str]] | None = None):
        address = urlsplit(self.origin.base_url)
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
        try:
            connection.putrequest("GET", path, skip_accept_encoding=True)
            for name, value in headers or []:
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_article_varies_on_language_and_region(self) -> None:
        status, headers, body = self.get(
            "/article", [("Accept-Language", "zh"), ("X-Region", "cn")]
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Vary"], "Accept-Language, X-Region")
        self.assertEqual(headers["Cache-Control"], "max-age=600")
        self.assertEqual(
            json.loads(body)["selected"],
            {"accept-language": ["zh"], "x-region": ["cn"]},
        )
        _, other_headers, other_body = self.get(
            "/article", [("Accept-Language", "en"), ("X-Region", "cn")]
        )
        self.assertNotEqual(body, other_body)
        self.assertNotEqual(headers["ETag"], other_headers["ETag"])

    def test_missing_and_empty_values_are_distinct(self) -> None:
        _, missing_headers, missing = self.get("/article")
        _, empty_headers, empty = self.get("/article", [("X-Region", "")])
        self.assertIsNone(json.loads(missing)["selected"]["x-region"])
        self.assertEqual(json.loads(empty)["selected"]["x-region"], [""])
        self.assertNotEqual(missing_headers["ETag"], empty_headers["ETag"])

    def test_duplicate_field_values_remain_ordered(self) -> None:
        _, _, body = self.get("/article", [("X-Region", "a"), ("x-region", "b")])
        self.assertEqual(json.loads(body)["selected"]["x-region"], ["a", "b"])
        self.assertEqual(self.origin.request_log[-1]["headers"]["x-region"], ["a", "b"])

    def test_conditional_request_returns_empty_304(self) -> None:
        _, first_headers, _ = self.get("/article", [("X-Region", "a")])
        status, headers, body = self.get(
            "/article", [("X-Region", "a"), ("If-None-Match", first_headers["ETag"])]
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(headers["ETag"], first_headers["ETag"])
        status, _, _ = self.get(
            "/article", [("X-Region", "b"), ("If-None-Match", first_headers["ETag"])]
        )
        self.assertEqual(status, 200)

    def test_revalidation_updates_metadata_without_vary(self) -> None:
        _, first_headers, _ = self.get("/revalidate")
        self.assertEqual(first_headers["Cache-Control"], "max-age=0")
        self.assertIn("Vary", first_headers)
        status, headers, body = self.get(
            "/revalidate", [("If-None-Match", first_headers["ETag"])]
        )
        self.assertEqual((status, body), (304, b""))
        self.assertEqual(headers["Cache-Control"], "max-age=600")
        self.assertEqual(headers["X-Revision"], "refreshed")
        self.assertNotIn("Vary", headers)

    def test_star_changes_each_time(self) -> None:
        _, first_headers, first_body = self.get("/star")
        status, headers, body = self.get("/star", [("If-None-Match", first_headers["ETag"])])
        self.assertEqual(status, 200)
        self.assertEqual(headers["Vary"], "*")
        self.assertNotEqual(first_headers["ETag"], headers["ETag"])
        self.assertGreater(json.loads(body)["sequence"], json.loads(first_body)["sequence"])

    def test_plain_ignores_request_selection_fields(self) -> None:
        _, first_headers, first = self.get("/plain", [("X-Region", "a")])
        _, headers, body = self.get("/plain", [("X-Region", "b")])
        self.assertNotIn("Vary", headers)
        self.assertEqual(body, first)
        self.assertEqual(headers["ETag"], first_headers["ETag"])

    def test_switch_modes_change_selection_rule(self) -> None:
        expected = {"region": "X-Region", "language": "Accept-Language", "plain": None, "star": "*"}
        for mode, vary in expected.items():
            with self.subTest(mode=mode):
                self.origin.set_mode(mode)
                status, headers, body = self.get(
                    "/switch", [("Accept-Language", "zh"), ("X-Region", "cn")]
                )
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Vary"), vary)
                self.assertEqual(json.loads(body)["mode"], mode)
        with self.assertRaises(ValueError):
            self.origin.set_mode("unsupported")

    def test_loopback_and_request_log(self) -> None:
        self.assertEqual(urlsplit(self.origin.base_url).hostname, "127.0.0.1")
        status, _, _ = self.get("/missing?q=1", [("X-Probe", "fixture")])
        self.assertEqual(status, 404)
        record = self.origin.request_log[-1]
        self.assertEqual(record["path"], "/missing")
        self.assertEqual(record["target"], "/missing?q=1")
        self.assertEqual(record["headers"]["x-probe"], ["fixture"])
        self.assertEqual(record["status"], 404)


if __name__ == "__main__":
    unittest.main()
