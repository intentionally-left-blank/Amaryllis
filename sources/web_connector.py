from __future__ import annotations

import html
import re
from typing import Any

import httpx

from sources.base import SourceConnector, SourceItem, SourceQuery, canonical_source_id, utc_now_iso

_RESULT_PATTERN = re.compile(r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>')


class WebSourceConnector(SourceConnector):
    name = "web"

    def __init__(self, *, base_url: str = "https://duckduckgo.com/html/") -> None:
        self.base_url = str(base_url or "https://duckduckgo.com/html/").strip()

    def health(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "status": "ok",
            "transport": "http",
            "detail": "duckduckgo_html",
        }

    def search(self, query: SourceQuery) -> list[SourceItem]:
        normalized_query = str(query.query or "").strip()
        if not normalized_query:
            raise ValueError("query is required")
        limit = max(1, min(int(query.limit), 50))
        response = httpx.get(self.base_url, params={"q": normalized_query}, timeout=15.0)
        response.raise_for_status()

        results: list[SourceItem] = []
        for href, title_html in _RESULT_PATTERN.findall(response.text)[:limit]:
            title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
            url = str(href or "").strip()
            if not url or not title:
                continue
            results.append(
                SourceItem(
                    source=self.name,
                    canonical_id=canonical_source_id(source=self.name, url=url, title=title),
                    url=url,
                    title=title,
                    published_at=utc_now_iso(),
                    excerpt=None,
                    author=None,
                    raw_score=None,
                    metadata={
                        "query": normalized_query,
                        "connector": "duckduckgo_html",
                    },
                )
            )
        return results

