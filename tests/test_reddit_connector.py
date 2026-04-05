from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from sources.base import SourceQuery
from sources.reddit_connector import RedditSourceConnector


def _search_response(*, status_code: int, children: list[dict], headers: dict[str, str] | None = None) -> httpx.Response:
    payload = {
        "data": {
            "children": [{"kind": "t3", "data": item} for item in children],
        }
    }
    return httpx.Response(
        status_code=status_code,
        headers=headers or {},
        json=payload,
        request=httpx.Request("GET", "https://example.com/reddit-search"),
    )


class RedditSourceConnectorTests(unittest.TestCase):
    def test_public_search_parses_items_and_applies_filters(self) -> None:
        connector = RedditSourceConnector(
            oauth_client_id=None,
            oauth_client_secret=None,
            max_retries=0,
            retry_backoff_sec=0.0,
        )
        payload_items = [
            {
                "name": "t3_abc123",
                "title": "AI update from lab",
                "author": "alice",
                "subreddit": "MachineLearning",
                "url": "https://example.org/post",
                "permalink": "/r/MachineLearning/comments/abc123/ai_update",
                "created_utc": 1_775_000_000,
                "score": 123,
                "num_comments": 42,
                "upvote_ratio": 0.95,
                "over_18": False,
                "selftext": "summary",
            },
            {
                "name": "t3_nsfw",
                "title": "Ignored nsfw",
                "author": "bob",
                "subreddit": "MachineLearning",
                "url": "https://example.org/nsfw",
                "permalink": "/r/MachineLearning/comments/nsfw/ignored",
                "created_utc": 1_775_000_001,
                "over_18": True,
            },
            {
                "name": "t3_other_sub",
                "title": "Ignored subreddit",
                "author": "bob",
                "subreddit": "gaming",
                "url": "https://example.org/gaming",
                "permalink": "/r/gaming/comments/other/ignored",
                "created_utc": 1_775_000_002,
                "over_18": False,
            },
        ]
        response = _search_response(status_code=200, children=payload_items)

        with patch("sources.reddit_connector.httpx.get", return_value=response) as mock_get:
            items = connector.search(
                SourceQuery(
                    query="AI",
                    limit=20,
                    window_hours=24,
                    metadata={
                        "query_bundle": ["ai agents"],
                        "include_subreddits": ["MachineLearning"],
                    },
                )
            )

        self.assertEqual(len(items), 1)
        first = items[0]
        self.assertEqual(first.canonical_id, "t3_abc123")
        self.assertEqual(first.url, "https://example.org/post")
        self.assertEqual(first.author, "alice")
        self.assertEqual(first.metadata.get("subreddit"), "MachineLearning")
        self.assertEqual(first.metadata.get("auth_mode"), "public")
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.args[0], "https://www.reddit.com/search.json")

    def test_oauth_search_uses_token_and_oauth_endpoint(self) -> None:
        connector = RedditSourceConnector(
            oauth_client_id="client-id",
            oauth_client_secret="client-secret",
            max_retries=0,
            retry_backoff_sec=0.0,
        )
        token_response = httpx.Response(
            status_code=200,
            json={"access_token": "token-123", "expires_in": 3600},
            request=httpx.Request("POST", "https://example.com/reddit-token"),
        )
        search_response = _search_response(
            status_code=200,
            children=[
                {
                    "name": "t3_token",
                    "title": "OAuth item",
                    "author": "alice",
                    "subreddit": "MachineLearning",
                    "url": "https://example.org/oauth",
                    "permalink": "/r/MachineLearning/comments/token/oauth_item",
                    "created_utc": 1_775_000_100,
                    "over_18": False,
                }
            ],
        )

        with patch("sources.reddit_connector.httpx.post", return_value=token_response) as mock_post:
            with patch("sources.reddit_connector.httpx.get", return_value=search_response) as mock_get:
                items = connector.search(SourceQuery(query="AI", limit=10, window_hours=24))

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].metadata.get("auth_mode"), "oauth")
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(mock_get.call_args.args[0], "https://oauth.reddit.com/search")
        headers = mock_get.call_args.kwargs.get("headers") or {}
        self.assertEqual(headers.get("Authorization"), "Bearer token-123")

    def test_search_retries_after_rate_limit(self) -> None:
        connector = RedditSourceConnector(
            oauth_client_id=None,
            oauth_client_secret=None,
            max_retries=1,
            retry_backoff_sec=0.01,
        )
        rate_limited = _search_response(status_code=429, children=[], headers={"Retry-After": "0.25"})
        success = _search_response(
            status_code=200,
            children=[
                {
                    "name": "t3_ok",
                    "title": "Recovered item",
                    "author": "alice",
                    "subreddit": "MachineLearning",
                    "url": "https://example.org/recovered",
                    "permalink": "/r/MachineLearning/comments/ok/recovered",
                    "created_utc": 1_775_000_200,
                    "over_18": False,
                }
            ],
        )

        with patch("sources.reddit_connector.httpx.get", side_effect=[rate_limited, success]) as mock_get:
            with patch("sources.reddit_connector.time.sleep") as mock_sleep:
                items = connector.search(SourceQuery(query="AI", limit=10, window_hours=24))

        self.assertEqual(len(items), 1)
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)


if __name__ == "__main__":
    unittest.main()
