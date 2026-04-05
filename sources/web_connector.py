from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from sources.base import SourceConnector, SourceItem, SourceQuery, canonical_source_id, utc_now_iso

_RESULT_LINK_PATTERN = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_SNIPPET_PATTERN = re.compile(
    r'<(?:a|div)[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</(?:a|div)>',
    re.IGNORECASE | re.DOTALL,
)
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
_TITLE_TAG_PATTERN = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)


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


def _normalize_result_url(raw_url: str) -> str:
    value = str(raw_url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urlparse(value)
    host = str(parsed.netloc or "").strip().lower()
    if host.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        params = parse_qs(parsed.query or "")
        uddg = params.get("uddg")
        if uddg:
            resolved = _trimmed_or_none(unquote(str(uddg[0] or "")))
            if resolved:
                return resolved
    return value


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
            hits = self._parse_search_hits(response.text, max_results=max(1, per_query_limit))
            for rank, hit in enumerate(hits, start=1):
                raw_title = str(hit.get("title") or "")
                title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
                title_from_search = bool(title)
                search_snippet = _trimmed_or_none(str(hit.get("snippet") or ""))
                url = _normalize_result_url(str(hit.get("href") or ""))
                if not url:
                    continue
                parsed = urlparse(url)
                host = str(parsed.netloc or "").strip().lower()
                if include_domains and not any(host == domain or host.endswith(f".{domain}") for domain in include_domains):
                    continue
                if exclude_domains and any(host == domain or host.endswith(f".{domain}") for domain in exclude_domains):
                    continue
                fetch_payload = self._fetch_extract(url=url, enabled=fetch_content, search_snippet=search_snippet)
                title_hint = _trimmed_or_none(str(fetch_payload.get("title_hint") or ""))
                if not title and title_hint:
                    title = title_hint
                if not title:
                    continue
                key = canonical_source_id(source=self.name, url=url, title=title)
                if key in seen:
                    continue
                excerpt = _trimmed_or_none(str(fetch_payload.get("excerpt") or ""))
                excerpt_source = _trimmed_or_none(str(fetch_payload.get("excerpt_source") or ""))
                if excerpt is None and search_snippet:
                    excerpt = search_snippet[:500]
                    excerpt_source = "search_snippet"
                seen.add(key)
                results.append(
                    SourceItem(
                        source=self.name,
                        canonical_id=key,
                        url=url,
                        title=title,
                        published_at=str(fetch_payload.get("published_at") or utc_now_iso()),
                        excerpt=excerpt,
                        author=None,
                        raw_score=None,
                        metadata={
                            "query": normalized_query,
                            "matched_query": query_text,
                            "connector": "duckduckgo_html",
                            "search_rank": rank,
                            "search_snippet": search_snippet,
                            "fetch_status": fetch_payload.get("status"),
                            "fetch_status_code": fetch_payload.get("status_code"),
                            "fetch_final_url": fetch_payload.get("final_url"),
                            "fetch_content_type": fetch_payload.get("content_type"),
                            "published_hint": fetch_payload.get("published_at"),
                            "excerpt_source": excerpt_source,
                            "title_source": "search_result" if title_from_search else "page_title",
                        },
                    )
                )
        return results

    def _parse_search_hits(self, html_text: str, *, max_results: int) -> list[dict[str, str]]:
        hits: list[dict[str, str]] = []
        for match in _RESULT_LINK_PATTERN.finditer(html_text):
            href = str(match.group("href") or "").strip()
            title_html = str(match.group("title") or "")
            next_match = _RESULT_LINK_PATTERN.search(html_text, match.end())
            snippet_window_end = next_match.start() if next_match is not None else len(html_text)
            snippet_window = html_text[match.end() : snippet_window_end]
            snippet_match = _RESULT_SNIPPET_PATTERN.search(snippet_window)
            snippet_raw = str(snippet_match.group("snippet") or "") if snippet_match is not None else ""
            snippet_text = _trimmed_or_none(_strip_html(snippet_raw))
            hits.append(
                {
                    "href": href,
                    "title": title_html,
                    "snippet": snippet_text or "",
                }
            )
            if len(hits) >= max(1, max_results):
                break
        return hits

    def _fetch_extract(self, *, url: str, enabled: bool, search_snippet: str | None = None) -> dict[str, Any]:
        if not enabled:
            return {
                "status": "skipped",
                "status_code": None,
                "final_url": None,
                "content_type": None,
                "title_hint": None,
                "published_at": None,
                "excerpt": search_snippet,
                "excerpt_source": "search_snippet" if search_snippet else None,
            }
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
            excerpt, excerpt_source = self._extract_excerpt(html_text, search_snippet=search_snippet)
            title_hint = self._extract_title_hint(html_text)
            return {
                "status": "ok",
                "status_code": int(response.status_code),
                "final_url": str(response.url),
                "content_type": str(response.headers.get("content-type") or "").split(";")[0].strip().lower() or None,
                "title_hint": title_hint,
                "published_at": published_hint,
                "excerpt": excerpt,
                "excerpt_source": excerpt_source,
            }
        except Exception as exc:
            return {
                "status": f"error:{type(exc).__name__}",
                "status_code": None,
                "final_url": None,
                "content_type": None,
                "title_hint": None,
                "published_at": None,
                "excerpt": search_snippet,
                "excerpt_source": "search_snippet" if search_snippet else None,
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

    def _extract_title_hint(self, html_text: str) -> str | None:
        match = _TITLE_TAG_PATTERN.search(html_text)
        if match:
            value = _trimmed_or_none(_strip_html(match.group("title")))
            if value:
                return value[:300]
        return None

    def _extract_excerpt(self, html_text: str, *, search_snippet: str | None = None) -> tuple[str | None, str | None]:
        match = _META_DESCRIPTION_PATTERN.search(html_text)
        if match:
            value = _strip_html(match.group("content"))
            if value:
                return value[:500], "meta_description"

        for paragraph_match in _PARAGRAPH_PATTERN.finditer(html_text):
            candidate = _strip_html(paragraph_match.group("text"))
            if len(candidate) >= 40:
                return candidate[:500], "paragraph"
        snippet = _trimmed_or_none(search_snippet)
        if snippet:
            return snippet[:500], "search_snippet"
        return None, None
