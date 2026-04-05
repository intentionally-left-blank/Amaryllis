from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from sources.base import SourceConnector, SourceItem, SourceQuery, canonical_source_id, utc_now_iso

_RESULT_PATTERN = re.compile(r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>')
_META_DESCRIPTION_PATTERN = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]*content=["\'](?P<content>[^"\']+)["\']',
    re.IGNORECASE,
)
_PUBLISH_HINT_PATTERN = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:article:published_time|og:published_time|pubdate|publishdate)["\'][^>]*content=["\'](?P<value>[^"\']+)["\']',
    re.IGNORECASE,
)
_TIME_TAG_PATTERN = re.compile(r'<time[^>]*datetime=["\'](?P<value>[^"\']+)["\']', re.IGNORECASE)
_PARAGRAPH_PATTERN = re.compile(r"<p[^>]*>(?P<text>.*?)</p>", re.IGNORECASE | re.DOTALL)


def _strip_html(raw: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _trimmed_or_none(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    return value or None


class WebSourceConnector(SourceConnector):
    name = "web"

    def __init__(
        self,
        *,
        base_url: str = "https://duckduckgo.com/html/",
        search_timeout_sec: float = 15.0,
        fetch_timeout_sec: float = 8.0,
        fetch_enabled_by_default: bool = True,
        max_fetch_chars: int = 400_000,
    ) -> None:
        self.base_url = str(base_url or "https://duckduckgo.com/html/").strip()
        self.search_timeout_sec = max(1.0, float(search_timeout_sec))
        self.fetch_timeout_sec = max(1.0, float(fetch_timeout_sec))
        self.fetch_enabled_by_default = bool(fetch_enabled_by_default)
        self.max_fetch_chars = max(20_000, int(max_fetch_chars))

    def health(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "status": "ok",
            "transport": "http",
            "detail": "duckduckgo_html_fetch_extract",
            "fetch_extract_enabled": self.fetch_enabled_by_default,
            "fetch_timeout_sec": self.fetch_timeout_sec,
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

        fetch_content = bool(metadata.get("fetch_content", self.fetch_enabled_by_default))

        results: list[SourceItem] = []
        seen: set[str] = set()
        per_query_limit = max(1, int(limit / max(1, len(query_bundle))))
        for query_text in query_bundle:
            response = httpx.get(self.base_url, params={"q": query_text}, timeout=self.search_timeout_sec)
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
                fetch_payload = self._fetch_extract(url=url, enabled=fetch_content)
                seen.add(key)
                results.append(
                    SourceItem(
                        source=self.name,
                        canonical_id=key,
                        url=url,
                        title=title,
                        published_at=str(fetch_payload.get("published_at") or utc_now_iso()),
                        excerpt=_trimmed_or_none(str(fetch_payload.get("excerpt") or "")),
                        author=None,
                        raw_score=None,
                        metadata={
                            "query": normalized_query,
                            "matched_query": query_text,
                            "connector": "duckduckgo_html",
                            "fetch_status": fetch_payload.get("status"),
                            "published_hint": fetch_payload.get("published_at"),
                            "excerpt_source": fetch_payload.get("excerpt_source"),
                        },
                    )
                )
        return results

    def _fetch_extract(self, *, url: str, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return {"status": "skipped", "published_at": None, "excerpt": None, "excerpt_source": None}
        try:
            response = httpx.get(
                url,
                timeout=self.fetch_timeout_sec,
                follow_redirects=True,
                headers={"User-Agent": "amaryllis-news-agent/1.0"},
            )
            response.raise_for_status()
            html_text = str(response.text or "")
            if len(html_text) > self.max_fetch_chars:
                html_text = html_text[: self.max_fetch_chars]
            published_hint = self._extract_publish_hint(html_text)
            excerpt, excerpt_source = self._extract_excerpt(html_text)
            return {
                "status": "ok",
                "published_at": published_hint,
                "excerpt": excerpt,
                "excerpt_source": excerpt_source,
            }
        except Exception as exc:
            return {
                "status": f"error:{type(exc).__name__}",
                "published_at": None,
                "excerpt": None,
                "excerpt_source": None,
            }

    def _extract_publish_hint(self, html_text: str) -> str | None:
        match = _PUBLISH_HINT_PATTERN.search(html_text)
        if match:
            value = _trimmed_or_none(match.group("value"))
            if value:
                return value
        match = _TIME_TAG_PATTERN.search(html_text)
        if match:
            value = _trimmed_or_none(match.group("value"))
            if value:
                return value
        return None

    def _extract_excerpt(self, html_text: str) -> tuple[str | None, str | None]:
        match = _META_DESCRIPTION_PATTERN.search(html_text)
        if match:
            value = _strip_html(match.group("content"))
            if value:
                return value[:500], "meta_description"

        for paragraph_match in _PARAGRAPH_PATTERN.finditer(html_text):
            candidate = _strip_html(paragraph_match.group("text"))
            if len(candidate) >= 40:
                return candidate[:500], "paragraph"
        return None, None
