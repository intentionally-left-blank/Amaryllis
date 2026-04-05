from __future__ import annotations

from datetime import datetime, timezone
import os
import time
from typing import Any

import httpx

from sources.base import SourceConnector, SourceItem, SourceQuery
from sources.base import canonical_source_id, utc_now_iso

_REDDIT_PUBLIC_SEARCH_URL = "https://www.reddit.com/search.json"
_REDDIT_OAUTH_SEARCH_URL = "https://oauth.reddit.com/search"
_REDDIT_OAUTH_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"


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


def _created_to_iso(raw_value: Any) -> str:
    try:
        epoch = float(raw_value)
    except Exception:
        return utc_now_iso()
    if epoch < 0:
        epoch = 0.0
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


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


def _window_to_time_filter(window_hours: int) -> str:
    if window_hours <= 1:
        return "hour"
    if window_hours <= 24:
        return "day"
    if window_hours <= 24 * 7:
        return "week"
    if window_hours <= 24 * 31:
        return "month"
    if window_hours <= 24 * 365:
        return "year"
    return "all"


class RedditSourceConnector(SourceConnector):
    name = "reddit"

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        oauth_client_id: str | None = None,
        oauth_client_secret: str | None = None,
        oauth_refresh_token: str | None = None,
        timeout_sec: float = 15.0,
        max_retries: int = 2,
        retry_backoff_sec: float = 0.5,
        public_search_url: str = _REDDIT_PUBLIC_SEARCH_URL,
        oauth_search_url: str = _REDDIT_OAUTH_SEARCH_URL,
        oauth_token_url: str = _REDDIT_OAUTH_TOKEN_URL,
    ) -> None:
        self.user_agent = _trimmed_or_none(user_agent) or str(
            os.getenv("AMARYLLIS_REDDIT_USER_AGENT", "amaryllis-news-agent/1.0")
        ).strip()
        self.oauth_client_id = _trimmed_or_none(oauth_client_id) or _trimmed_or_none(
            os.getenv("AMARYLLIS_REDDIT_CLIENT_ID")
        )
        self.oauth_client_secret = _trimmed_or_none(oauth_client_secret) or _trimmed_or_none(
            os.getenv("AMARYLLIS_REDDIT_CLIENT_SECRET")
        )
        self.oauth_refresh_token = _trimmed_or_none(oauth_refresh_token) or _trimmed_or_none(
            os.getenv("AMARYLLIS_REDDIT_REFRESH_TOKEN")
        )
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_sec = max(0.0, float(retry_backoff_sec))
        self.public_search_url = str(public_search_url or _REDDIT_PUBLIC_SEARCH_URL).strip()
        self.oauth_search_url = str(oauth_search_url or _REDDIT_OAUTH_SEARCH_URL).strip()
        self.oauth_token_url = str(oauth_token_url or _REDDIT_OAUTH_TOKEN_URL).strip()
        self._oauth_access_token: str | None = None
        self._oauth_expires_at: float = 0.0

    def _oauth_configured(self) -> bool:
        return bool(self.oauth_client_id and self.oauth_client_secret)

    def health(self) -> dict[str, Any]:
        mode = "oauth" if self._oauth_configured() else "public"
        return {
            "source": self.name,
            "status": "ok",
            "mode": mode,
            "oauth_configured": self._oauth_configured(),
            "detail": "reddit_search_json",
            "retry": {
                "max_retries": self.max_retries,
                "backoff_sec": self.retry_backoff_sec,
            },
        }

    def search(self, query: SourceQuery) -> list[SourceItem]:
        normalized_query = str(query.query or "").strip()
        if not normalized_query:
            raise ValueError("query is required")

        limit = max(1, min(int(query.limit), 100))
        window_hours = max(1, min(int(query.window_hours), 24 * 365 * 2))
        metadata = query.metadata if isinstance(query.metadata, dict) else {}
        query_bundle_raw = metadata.get("query_bundle")
        query_bundle = _normalize_string_list(query_bundle_raw)
        if not query_bundle:
            query_bundle = [normalized_query]

        source_overrides = metadata.get("source_overrides")
        if not isinstance(source_overrides, dict):
            scope = metadata.get("scope")
            source_overrides = scope.get("source_overrides") if isinstance(scope, dict) else {}
        reddit_overrides = source_overrides.get("reddit") if isinstance(source_overrides, dict) else {}
        if not isinstance(reddit_overrides, dict):
            reddit_overrides = {}

        include_subreddits = _normalize_string_list(
            metadata.get("include_subreddits") or reddit_overrides.get("include_subreddits") or reddit_overrides.get("subreddits")
        )
        exclude_subreddits = _normalize_string_list(
            metadata.get("exclude_subreddits") or reddit_overrides.get("exclude_subreddits")
        )
        include_subreddits_set = {item.lower() for item in include_subreddits}
        exclude_subreddits_set = {item.lower() for item in exclude_subreddits}
        allow_nsfw = bool(metadata.get("allow_nsfw", reddit_overrides.get("allow_nsfw", False)))

        results: list[SourceItem] = []
        seen: set[str] = set()
        per_query_limit = max(1, min(100, int(limit / max(1, len(query_bundle))) or 1))
        for query_text in query_bundle:
            payload, auth_mode = self._search_once(
                query=query_text,
                window_hours=window_hours,
                limit=per_query_limit,
            )
            children = payload.get("data", {}).get("children", [])
            if not isinstance(children, list):
                continue
            for child in children:
                if not isinstance(child, dict):
                    continue
                post = child.get("data")
                if not isinstance(post, dict):
                    continue
                subreddit = str(post.get("subreddit") or "").strip()
                subreddit_key = subreddit.lower()
                if include_subreddits_set and subreddit_key not in include_subreddits_set:
                    continue
                if exclude_subreddits_set and subreddit_key in exclude_subreddits_set:
                    continue
                is_nsfw = bool(post.get("over_18"))
                if is_nsfw and not allow_nsfw:
                    continue

                title = str(post.get("title") or "").strip()
                author = str(post.get("author") or "").strip() or None
                canonical_name = str(post.get("name") or "").strip()
                permalink = str(post.get("permalink") or "").strip()
                post_url = str(post.get("url") or "").strip()
                if permalink.startswith("/"):
                    permalink = f"https://www.reddit.com{permalink}"
                url = post_url or permalink
                if not title or not url:
                    continue

                canonical_id = canonical_name or canonical_source_id(source=self.name, url=permalink or url, title=title)
                if canonical_id in seen:
                    continue
                seen.add(canonical_id)

                selftext = str(post.get("selftext") or "").strip()
                excerpt = selftext[:400] if selftext else None
                score_raw = post.get("score")
                score: float | None
                try:
                    score = float(score_raw) if score_raw is not None else None
                except Exception:
                    score = None

                results.append(
                    SourceItem(
                        source=self.name,
                        canonical_id=canonical_id,
                        url=url,
                        title=title,
                        published_at=_created_to_iso(post.get("created_utc")),
                        excerpt=excerpt,
                        author=author,
                        raw_score=score,
                        metadata={
                            "subreddit": subreddit or None,
                            "query": normalized_query,
                            "matched_query": str(query_text).strip(),
                            "auth_mode": auth_mode,
                            "is_nsfw": is_nsfw,
                            "num_comments": post.get("num_comments"),
                            "upvote_ratio": post.get("upvote_ratio"),
                            "reddit_permalink": permalink or None,
                            "connector": "reddit_search_json",
                        },
                    )
                )
                if len(results) >= limit:
                    return results
        return results

    def _search_once(self, *, query: str, window_hours: int, limit: int) -> tuple[dict[str, Any], str]:
        params = {
            "q": query,
            "sort": "new",
            "limit": max(1, min(int(limit), 100)),
            "t": _window_to_time_filter(window_hours),
            "raw_json": "1",
            "type": "link",
        }
        if self._oauth_configured():
            token = self._oauth_access_token_or_refresh()
            if token:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "User-Agent": self.user_agent,
                }
                try:
                    payload = self._request_json(self.oauth_search_url, params=params, headers=headers)
                    return payload, "oauth"
                except httpx.HTTPStatusError as exc:
                    if exc.response is not None and exc.response.status_code in {401, 403}:
                        self._oauth_access_token = None
                        self._oauth_expires_at = 0.0
                    else:
                        raise
                except Exception:
                    pass

        headers = {"User-Agent": self.user_agent}
        payload = self._request_json(self.public_search_url, params=params, headers=headers)
        return payload, "public"

    def _oauth_access_token_or_refresh(self) -> str | None:
        now = time.time()
        if self._oauth_access_token and now < self._oauth_expires_at:
            return self._oauth_access_token
        if not self._oauth_configured():
            return None

        data = {"grant_type": "client_credentials"}
        if self.oauth_refresh_token:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self.oauth_refresh_token,
            }
        response = httpx.post(
            self.oauth_token_url,
            data=data,
            auth=(str(self.oauth_client_id), str(self.oauth_client_secret)),
            headers={
                "User-Agent": self.user_agent,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        access_token = _trimmed_or_none(str(payload.get("access_token") or ""))
        if not access_token:
            return None
        expires_in_raw = payload.get("expires_in", 3600)
        try:
            expires_in = max(60.0, float(expires_in_raw))
        except Exception:
            expires_in = 3600.0
        # Refresh a bit earlier than expiry to reduce 401 churn.
        self._oauth_access_token = access_token
        self._oauth_expires_at = time.time() + max(30.0, expires_in - 60.0)
        return self._oauth_access_token

    def _request_json(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
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
                raise ValueError("Reddit response is not a JSON object")
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
