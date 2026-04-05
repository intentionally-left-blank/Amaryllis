from __future__ import annotations

from datetime import datetime, timezone
import os
import time
from typing import Any

import httpx

from sources.base import SourceConnector, SourceItem, SourceQuery
from sources.base import utc_now_iso

_X_RECENT_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"


def _trimmed_or_none(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    return value or None


def _normalize_string_list(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(value)
    return result


def _retry_after_seconds(headers: Any) -> float | None:
    if not isinstance(headers, dict):
        return None
    value = str(headers.get("Retry-After") or headers.get("retry-after") or "").strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except Exception:
        return None
    return max(0.0, parsed)


def _to_iso_or_now(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if value:
        return value
    return utc_now_iso()


class XSourceConnector(SourceConnector):
    name = "x"

    def __init__(
        self,
        *,
        bearer_token: str | None = None,
        timeout_sec: float = 15.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        search_url: str = _X_RECENT_SEARCH_URL,
    ) -> None:
        self.bearer_token = _trimmed_or_none(bearer_token) or _trimmed_or_none(os.getenv("AMARYLLIS_X_BEARER_TOKEN"))
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_sec = max(0.0, float(retry_backoff_sec))
        self.search_url = str(search_url or _X_RECENT_SEARCH_URL).strip()

    def _is_configured(self) -> bool:
        return bool(self.bearer_token)

    def health(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "status": "ok" if self._is_configured() else "degraded",
            "auth_mode": "bearer",
            "configured": self._is_configured(),
            "detail": "x_recent_search_v2",
            "retry": {
                "max_retries": self.max_retries,
                "backoff_sec": self.retry_backoff_sec,
            },
        }

    def search(self, query: SourceQuery) -> list[SourceItem]:
        normalized_query = str(query.query or "").strip()
        if not normalized_query:
            raise ValueError("query is required")
        if not self._is_configured():
            raise ValueError("X connector requires AMARYLLIS_X_BEARER_TOKEN")

        limit = max(1, min(int(query.limit), 100))
        metadata = query.metadata if isinstance(query.metadata, dict) else {}
        query_bundle = _normalize_string_list(metadata.get("query_bundle"))
        if not query_bundle:
            query_bundle = [normalized_query]

        source_overrides = metadata.get("source_overrides")
        if not isinstance(source_overrides, dict):
            scope = metadata.get("scope")
            source_overrides = scope.get("source_overrides") if isinstance(scope, dict) else {}
        x_overrides = source_overrides.get("x") if isinstance(source_overrides, dict) else {}
        if not isinstance(x_overrides, dict):
            x_overrides = {}

        include_users = _normalize_string_list(
            metadata.get("include_users") or x_overrides.get("include_users")
        )
        exclude_users = _normalize_string_list(
            metadata.get("exclude_users") or x_overrides.get("exclude_users")
        )
        include_users_set = {item.lower().lstrip("@") for item in include_users}
        exclude_users_set = {item.lower().lstrip("@") for item in exclude_users}
        language_candidates = metadata.get("languages") or x_overrides.get("languages")
        if not language_candidates and query.language:
            language_candidates = [query.language]
        language_filters = _normalize_string_list(language_candidates)
        language_set = {item.lower() for item in language_filters}
        allow_sensitive = bool(metadata.get("allow_sensitive", x_overrides.get("allow_sensitive", False)))

        results: list[SourceItem] = []
        seen_ids: set[str] = set()
        per_query_limit = max(1, min(100, int(limit / max(1, len(query_bundle))) or 1))
        for query_text in query_bundle:
            payload = self._search_once(query=query_text, limit=per_query_limit)
            users_index = self._users_index(payload)
            tweets = payload.get("data")
            if not isinstance(tweets, list):
                continue
            for tweet in tweets:
                if not isinstance(tweet, dict):
                    continue
                tweet_id = str(tweet.get("id") or "").strip()
                if not tweet_id or tweet_id in seen_ids:
                    continue
                text = str(tweet.get("text") or "").strip()
                if not text:
                    continue

                author_id = str(tweet.get("author_id") or "").strip()
                author_info = users_index.get(author_id, {})
                username = str(author_info.get("username") or "").strip()
                username_key = username.lower().lstrip("@")
                if include_users_set and username_key not in include_users_set:
                    continue
                if exclude_users_set and username_key in exclude_users_set:
                    continue

                lang = str(tweet.get("lang") or "").strip().lower()
                if language_set and lang and lang not in language_set:
                    continue
                if bool(tweet.get("possibly_sensitive")) and not allow_sensitive:
                    continue

                seen_ids.add(tweet_id)
                profile_name = str(author_info.get("name") or "").strip()
                author_display = username or profile_name or author_id or None
                url = f"https://x.com/{username}/status/{tweet_id}" if username else f"https://x.com/i/web/status/{tweet_id}"
                title = text[:160]
                metrics = tweet.get("public_metrics") if isinstance(tweet.get("public_metrics"), dict) else {}
                score = self._score_from_metrics(metrics)

                results.append(
                    SourceItem(
                        source=self.name,
                        canonical_id=tweet_id,
                        url=url,
                        title=title,
                        published_at=_to_iso_or_now(tweet.get("created_at")),
                        excerpt=text[:500],
                        author=author_display,
                        raw_score=score,
                        metadata={
                            "query": normalized_query,
                            "matched_query": str(query_text).strip(),
                            "lang": lang or None,
                            "author_id": author_id or None,
                            "username": username or None,
                            "verified": bool(author_info.get("verified", False)),
                            "possibly_sensitive": bool(tweet.get("possibly_sensitive", False)),
                            "public_metrics": metrics,
                            "connector": "x_recent_search_v2",
                        },
                    )
                )
                if len(results) >= limit:
                    return results
        return results

    def _users_index(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        includes = payload.get("includes")
        if not isinstance(includes, dict):
            return {}
        users_raw = includes.get("users")
        if not isinstance(users_raw, list):
            return {}
        users: dict[str, dict[str, Any]] = {}
        for item in users_raw:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("id") or "").strip()
            if not user_id:
                continue
            users[user_id] = item
        return users

    def _search_once(self, *, query: str, limit: int) -> dict[str, Any]:
        params = {
            "query": query,
            "max_results": max(10, min(int(limit), 100)),
            "tweet.fields": "created_at,author_id,lang,possibly_sensitive,public_metrics",
            "expansions": "author_id",
            "user.fields": "id,username,name,verified",
            "sort_order": "recency",
        }
        headers = {
            "Authorization": f"Bearer {self.bearer_token}",
        }
        return self._request_json(url=self.search_url, params=params, headers=headers)

    def _request_json(self, *, url: str, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = httpx.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_sec,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    self._sleep_before_retry(attempt=attempt, headers=dict(response.headers))
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    return payload
                raise ValueError("X API response is not a JSON object")
            except (httpx.TransportError, httpx.TimeoutException, httpx.HTTPStatusError, ValueError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise
                sleep_headers: dict[str, Any] = {}
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                    sleep_headers = dict(exc.response.headers)
                self._sleep_before_retry(attempt=attempt, headers=sleep_headers)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("request failed")

    def _sleep_before_retry(self, *, attempt: int, headers: dict[str, Any]) -> None:
        retry_after = _retry_after_seconds(headers)
        if retry_after is not None:
            wait_sec = retry_after
        else:
            wait_sec = self.retry_backoff_sec * (2 ** max(0, int(attempt)))
        if wait_sec <= 0:
            return
        time.sleep(min(wait_sec, 5.0))

    def _score_from_metrics(self, metrics: dict[str, Any]) -> float | None:
        if not metrics:
            return None
        try:
            likes = float(metrics.get("like_count", 0) or 0)
            reposts = float(metrics.get("retweet_count", 0) or metrics.get("repost_count", 0) or 0)
            replies = float(metrics.get("reply_count", 0) or 0)
            quotes = float(metrics.get("quote_count", 0) or 0)
            return likes + (reposts * 1.5) + (replies * 1.1) + (quotes * 1.2)
        except Exception:
            return None
