from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from sources.base import SourceQuery
from sources.x_connector import XSourceConnector


def _search_response(
    *,
    status_code: int,
    tweets: list[dict],
    users: list[dict] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    payload: dict[str, object] = {"data": tweets}
    if users is not None:
        payload["includes"] = {"users": users}
    return httpx.Response(
        status_code=status_code,
        headers=headers or {},
        json=payload,
        request=httpx.Request("GET", "https://example.com/x-search"),
    )


class XSourceConnectorTests(unittest.TestCase):
    def test_search_requires_bearer_token(self) -> None:
        connector = XSourceConnector(bearer_token=None)
        with self.assertRaises(ValueError):
            connector.search(SourceQuery(query="AI"))

    def test_search_parses_tweets_and_applies_filters(self) -> None:
        connector = XSourceConnector(bearer_token="token-123", max_retries=0, retry_backoff_sec=0.0)
        response = _search_response(
            status_code=200,
            tweets=[
                {
                    "id": "123",
                    "text": "AI update",
                    "author_id": "u1",
                    "created_at": "2026-04-04T00:00:00Z",
                    "lang": "en",
                    "possibly_sensitive": False,
                    "public_metrics": {"like_count": 10, "retweet_count": 3, "reply_count": 2, "quote_count": 1},
                },
                {
                    "id": "nsfw",
                    "text": "Ignored sensitive",
                    "author_id": "u1",
                    "created_at": "2026-04-04T00:00:10Z",
                    "lang": "en",
                    "possibly_sensitive": True,
                },
                {
                    "id": "ru",
                    "text": "Ignored language",
                    "author_id": "u1",
                    "created_at": "2026-04-04T00:00:20Z",
                    "lang": "ru",
                    "possibly_sensitive": False,
                },
                {
                    "id": "other-user",
                    "text": "Ignored user",
                    "author_id": "u2",
                    "created_at": "2026-04-04T00:00:30Z",
                    "lang": "en",
                    "possibly_sensitive": False,
                },
            ],
            users=[
                {"id": "u1", "username": "OpenAI", "name": "OpenAI", "verified": True},
                {"id": "u2", "username": "SomeoneElse", "name": "Else", "verified": False},
            ],
        )

        with patch("sources.x_connector.httpx.get", return_value=response) as mock_get:
            items = connector.search(
                SourceQuery(
                    query="AI",
                    limit=20,
                    metadata={
                        "query_bundle": ["ai agents"],
                        "include_users": ["@openai"],
                        "languages": ["en"],
                    },
                )
            )

        self.assertEqual(len(items), 1)
        first = items[0]
        self.assertEqual(first.canonical_id, "123")
        self.assertEqual(first.url, "https://x.com/OpenAI/status/123")
        self.assertEqual(first.author, "OpenAI")
        self.assertEqual(first.metadata.get("username"), "OpenAI")
        self.assertEqual(first.metadata.get("connector"), "x_recent_search_v2")
        self.assertEqual(mock_get.call_count, 1)
        headers = mock_get.call_args.kwargs.get("headers") or {}
        self.assertEqual(headers.get("Authorization"), "Bearer token-123")

    def test_search_retries_after_rate_limit(self) -> None:
        connector = XSourceConnector(
            bearer_token="token-123",
            max_retries=1,
            retry_backoff_sec=0.01,
        )
        rate_limited = _search_response(status_code=429, tweets=[], headers={"Retry-After": "0.25"})
        success = _search_response(
            status_code=200,
            tweets=[
                {
                    "id": "ok",
                    "text": "Recovered",
                    "author_id": "u1",
                    "created_at": "2026-04-04T00:00:00Z",
                    "lang": "en",
                    "possibly_sensitive": False,
                }
            ],
            users=[{"id": "u1", "username": "OpenAI"}],
        )

        with patch("sources.x_connector.httpx.get", side_effect=[rate_limited, success]) as mock_get:
            with patch("sources.x_connector.time.sleep") as mock_sleep:
                items = connector.search(SourceQuery(query="AI", limit=10))

        self.assertEqual(len(items), 1)
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)


if __name__ == "__main__":
    unittest.main()
