from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from storage.database import Database


class NewsStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-news-storage-")
        self.db_path = Path(self._tmp.name) / "state.db"
        self.database = Database(self.db_path)

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_upsert_and_list_news_items_with_filters(self) -> None:
        count = self.database.upsert_news_items(
            user_id="user-1",
            topic="AI",
            items=[
                {
                    "source": "web",
                    "canonical_id": "web-1",
                    "url": "https://openai.com/blog/one",
                    "title": "One",
                    "excerpt": "item one",
                    "author": "team",
                    "published_at": "2026-04-04T00:00:00+00:00",
                    "ingested_at": "2026-04-04T00:01:00+00:00",
                    "raw_score": 0.8,
                    "metadata": {"matched_query": "ai site:openai.com"},
                },
                {
                    "source": "reddit",
                    "canonical_id": "reddit-1",
                    "url": "https://www.reddit.com/r/MachineLearning/comments/abc",
                    "title": "Two",
                    "published_at": "2026-04-04T00:05:00+00:00",
                    "ingested_at": "2026-04-04T00:06:00+00:00",
                    "metadata": {"subreddit": "MachineLearning"},
                },
            ],
        )
        self.assertEqual(count, 2)

        listed_all = self.database.list_news_items(user_id="user-1", topic="AI", limit=10)
        self.assertEqual(len(listed_all), 2)
        self.assertEqual(str(listed_all[0].get("source")), "reddit")
        self.assertEqual(str(listed_all[1].get("source")), "web")

        listed_web = self.database.list_news_items(user_id="user-1", topic="AI", source="web", limit=10)
        self.assertEqual(len(listed_web), 1)
        self.assertEqual(str(listed_web[0].get("url")), "https://openai.com/blog/one")
        self.assertEqual(str(listed_web[0].get("metadata", {}).get("matched_query")), "ai site:openai.com")
        self.assertEqual(float(listed_web[0].get("raw_score")), 0.8)

    def test_upsert_updates_existing_news_item(self) -> None:
        first = self.database.upsert_news_items(
            user_id="user-1",
            topic="AI",
            items=[
                {
                    "source": "web",
                    "canonical_id": "web-1",
                    "url": "https://openai.com/blog/one",
                    "title": "One",
                    "published_at": "2026-04-04T00:00:00+00:00",
                    "ingested_at": "2026-04-04T00:01:00+00:00",
                    "metadata": {"version": 1},
                }
            ],
        )
        self.assertEqual(first, 1)

        second = self.database.upsert_news_items(
            user_id="user-1",
            topic="AI",
            items=[
                {
                    "source": "web",
                    "canonical_id": "web-1",
                    "url": "https://openai.com/blog/one-updated",
                    "title": "One Updated",
                    "published_at": "2026-04-04T00:00:00+00:00",
                    "ingested_at": "2026-04-04T00:02:00+00:00",
                    "metadata": {"version": 2},
                }
            ],
        )
        self.assertEqual(second, 1)

        listed = self.database.list_news_items(user_id="user-1", topic="AI", source="web", limit=10)
        self.assertEqual(len(listed), 1)
        item = listed[0]
        self.assertEqual(str(item.get("url")), "https://openai.com/blog/one-updated")
        self.assertEqual(str(item.get("title")), "One Updated")
        self.assertEqual(int(item.get("metadata", {}).get("version")), 2)


if __name__ == "__main__":
    unittest.main()
