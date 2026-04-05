from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from sources.base import SourceQuery
from sources.web_connector import WebSourceConnector


def _ddg_response(html_body: str) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        text=html_body,
        request=httpx.Request("GET", "https://duckduckgo.com/html/"),
    )


class WebSourceConnectorTests(unittest.TestCase):
    def test_search_fetches_and_extracts_excerpt_and_publish_hint(self) -> None:
        connector = WebSourceConnector(base_url="https://duckduckgo.com/html/")
        ddg_html = (
            '<a class="result__a" href="https://example.com/story">AI Story</a>'
        )
        page_html = """
        <html>
          <head>
            <meta name="description" content="Daily AI update from example." />
            <meta property="article:published_time" content="2026-04-04T08:00:00Z" />
          </head>
          <body><p>Fallback paragraph text.</p></body>
        </html>
        """

        def fake_get(url: str, **kwargs):  # noqa: ANN001
            if "duckduckgo.com/html" in url:
                return _ddg_response(ddg_html)
            if "example.com/story" in url:
                return httpx.Response(
                    status_code=200,
                    text=page_html,
                    request=httpx.Request("GET", "https://example.com/story"),
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("sources.web_connector.httpx.get", side_effect=fake_get) as mock_get:
            items = connector.search(SourceQuery(query="AI", limit=5))

        self.assertEqual(len(items), 1)
        first = items[0]
        self.assertEqual(first.url, "https://example.com/story")
        self.assertEqual(first.excerpt, "Daily AI update from example.")
        self.assertEqual(first.published_at, "2026-04-04T08:00:00Z")
        self.assertEqual(first.metadata.get("fetch_status"), "ok")
        self.assertEqual(first.metadata.get("fetch_status_code"), 200)
        self.assertEqual(first.metadata.get("fetch_final_url"), "https://example.com/story")
        self.assertEqual(first.metadata.get("fetch_content_type"), "text/plain")
        self.assertEqual(first.metadata.get("excerpt_source"), "meta_description")
        self.assertEqual(mock_get.call_count, 2)

    def test_search_can_skip_fetch_extract(self) -> None:
        connector = WebSourceConnector(base_url="https://duckduckgo.com/html/")
        ddg_html = '<a class="result__a" href="https://example.com/story">AI Story</a>'

        with patch("sources.web_connector.httpx.get", return_value=_ddg_response(ddg_html)) as mock_get:
            items = connector.search(
                SourceQuery(
                    query="AI",
                    limit=5,
                    metadata={"fetch_content": False},
                )
            )

        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].excerpt)
        self.assertEqual(items[0].metadata.get("fetch_status"), "skipped")
        self.assertEqual(items[0].metadata.get("fetch_status_code"), None)
        self.assertEqual(mock_get.call_count, 1)

    def test_search_reports_fetch_error_status(self) -> None:
        connector = WebSourceConnector(base_url="https://duckduckgo.com/html/")
        ddg_html = '<a class="result__a" href="https://example.com/story">AI Story</a>'

        def fake_get(url: str, **kwargs):  # noqa: ANN001
            if "duckduckgo.com/html" in url:
                return _ddg_response(ddg_html)
            raise httpx.ConnectError("connection failed", request=httpx.Request("GET", url))

        with patch("sources.web_connector.httpx.get", side_effect=fake_get):
            items = connector.search(SourceQuery(query="AI", limit=5))

        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].excerpt)
        self.assertTrue(str(items[0].metadata.get("fetch_status")).startswith("error:"))

    def test_search_resolves_duckduckgo_redirect_url_and_snippet_fallback(self) -> None:
        connector = WebSourceConnector(base_url="https://duckduckgo.com/html/")
        ddg_html = (
            '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fstory">Story</a>'
            '<a class="result__snippet">Short snippet from search result for fallback.</a>'
        )

        def fake_get(url: str, **kwargs):  # noqa: ANN001
            if "duckduckgo.com/html" in url:
                return _ddg_response(ddg_html)
            if "example.com/story" in url:
                return httpx.Response(
                    status_code=200,
                    text="<html><head><title>Story title page</title></head><body></body></html>",
                    request=httpx.Request("GET", "https://example.com/story"),
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("sources.web_connector.httpx.get", side_effect=fake_get):
            items = connector.search(SourceQuery(query="AI", limit=5))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://example.com/story")
        self.assertEqual(items[0].excerpt, "Short snippet from search result for fallback.")
        self.assertEqual(items[0].metadata.get("excerpt_source"), "search_snippet")

    def test_search_uses_page_title_when_search_title_missing(self) -> None:
        connector = WebSourceConnector(base_url="https://duckduckgo.com/html/")
        ddg_html = '<a class="result__a" href="https://example.com/story"></a>'

        def fake_get(url: str, **kwargs):  # noqa: ANN001
            if "duckduckgo.com/html" in url:
                return _ddg_response(ddg_html)
            if "example.com/story" in url:
                return httpx.Response(
                    status_code=200,
                    text="<html><head><title>Fallback title from page</title></head><body></body></html>",
                    request=httpx.Request("GET", "https://example.com/story"),
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with patch("sources.web_connector.httpx.get", side_effect=fake_get):
            items = connector.search(SourceQuery(query="AI", limit=5))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Fallback title from page")
        self.assertEqual(items[0].metadata.get("title_source"), "page_title")


if __name__ == "__main__":
    unittest.main()
