from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

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
        metadata = query.metadata if isinstance(query.metadata, dict) else {}
        query_bundle_raw = metadata.get("query_bundle")
        if isinstance(query_bundle_raw, list):
            query_bundle = [str(item).strip() for item in query_bundle_raw if str(item).strip()]
        else:
            query_bundle = []
        if not query_bundle:
            query_bundle = [normalized_query]
        include_domains = {
            str(item).strip().lower().lstrip(".")
            for item in (metadata.get("include_domains") or [])
            if str(item).strip()
        }
        exclude_domains = {
            str(item).strip().lower().lstrip(".")
            for item in (metadata.get("exclude_domains") or [])
            if str(item).strip()
        }

        results: list[SourceItem] = []
        seen: set[str] = set()
        per_query_limit = max(1, int(limit / max(1, len(query_bundle))))
        for query_text in query_bundle:
            response = httpx.get(self.base_url, params={"q": query_text}, timeout=15.0)
            response.raise_for_status()
            hits = _RESULT_PATTERN.findall(response.text)[: max(1, per_query_limit)]
            for href, title_html in hits:
                title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
                url = str(href or "").strip()
                if not url or not title:
                    continue
                key = canonical_source_id(source=self.name, url=url, title=title)
                if key in seen:
                    continue
                parsed = urlparse(url)
                host = str(parsed.netloc or "").strip().lower()
                if include_domains and not any(host == domain or host.endswith(f".{domain}") for domain in include_domains):
                    continue
                if exclude_domains and any(host == domain or host.endswith(f".{domain}") for domain in exclude_domains):
                    continue
                seen.add(key)
                results.append(
                    SourceItem(
                        source=self.name,
                        canonical_id=key,
                        url=url,
                        title=title,
                        published_at=utc_now_iso(),
                        excerpt=None,
                        author=None,
                        raw_score=None,
                        metadata={
                            "query": normalized_query,
                            "matched_query": query_text,
                            "connector": "duckduckgo_html",
                        },
                    )
                )
        return results
