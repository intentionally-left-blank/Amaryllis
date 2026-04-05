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
        self.assertEqual(str(listed_web[0].get("canonical_story_key")), "https://openai.com/blog/one")
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
        self.assertEqual(str(item.get("canonical_story_key")), "https://openai.com/blog/one-updated")

    def test_upsert_preserves_merged_provenance_metadata(self) -> None:
        inserted = self.database.upsert_news_items(
            user_id="user-1",
            topic="AI",
            items=[
                {
                    "source": "web",
                    "canonical_id": "web-merged",
                    "url": "https://example.com/story",
                    "title": "Merged story",
                    "published_at": "2026-04-04T00:00:00+00:00",
                    "ingested_at": "2026-04-04T00:03:00+00:00",
                    "metadata": {
                        "merged_sources": ["web", "reddit"],
                        "merged_count": 2,
                        "provenance": [
                            {
                                "source": "web",
                                "canonical_id": "web-merged",
                                "url": "https://example.com/story",
                            },
                            {
                                "source": "reddit",
                                "canonical_id": "t3_abc123",
                                "url": "https://example.com/story",
                            },
                        ],
                    },
                }
            ],
        )
        self.assertEqual(inserted, 1)

        listed = self.database.list_news_items(user_id="user-1", topic="AI", source="web", limit=10)
        self.assertEqual(len(listed), 1)
        metadata = listed[0].get("metadata")
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata.get("merged_sources"), ["web", "reddit"])
        self.assertEqual(metadata.get("merged_count"), 2)
        provenance = metadata.get("provenance")
        self.assertIsInstance(provenance, list)
        self.assertEqual(len(provenance), 2)
        self.assertEqual(str(listed[0].get("canonical_story_key")), "https://example.com/story")

    def test_upsert_merges_cross_source_items_by_story_key(self) -> None:
        first = self.database.upsert_news_items(
            user_id="user-1",
            topic="AI",
            items=[
                {
                    "source": "web",
                    "canonical_id": "web-story",
                    "url": "https://example.com/story?utm_source=newsletter",
                    "title": "Shared Story",
                    "published_at": "2026-04-04T00:00:00+00:00",
                    "ingested_at": "2026-04-04T00:01:00+00:00",
                    "metadata": {},
                }
            ],
        )
        self.assertEqual(first, 1)

        second = self.database.upsert_news_items(
            user_id="user-1",
            topic="AI",
            items=[
                {
                    "source": "reddit",
                    "canonical_id": "t3_12345",
                    "url": "https://example.com/story",
                    "title": "Shared Story Discussion",
                    "published_at": "2026-04-04T00:05:00+00:00",
                    "ingested_at": "2026-04-04T00:06:00+00:00",
                    "metadata": {"subreddit": "MachineLearning"},
                }
            ],
        )
        self.assertEqual(second, 1)

        listed = self.database.list_news_items(user_id="user-1", topic="AI", limit=10)
        self.assertEqual(len(listed), 1)
        item = listed[0]
        self.assertEqual(str(item.get("source")), "web")
        self.assertEqual(str(item.get("canonical_story_key")), "https://example.com/story")
        self.assertEqual(str(item.get("url")), "https://example.com/story")
        metadata = item.get("metadata")
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata.get("merged_sources"), ["web", "reddit"])
        self.assertEqual(metadata.get("merged_count"), 2)
        provenance = metadata.get("provenance")
        self.assertIsInstance(provenance, list)
        self.assertEqual(len(provenance), 2)
        self.assertEqual(
            {str(entry.get("source")) for entry in provenance if isinstance(entry, dict)},
            {"web", "reddit"},
        )

    def test_upsert_and_list_news_delivery_policies(self) -> None:
        stored = self.database.upsert_news_delivery_policies(
            user_id="user-1",
            topic="AI",
            channels=[
                {
                    "channel": "webhook",
                    "enabled": True,
                    "max_targets": 2,
                    "targets": [
                        "https://example.com/hooks/news-main",
                        "https://example.com/hooks/news-secondary",
                        "https://example.com/hooks/news-ignored",
                    ],
                    "options": {"headers": {"X-Test": "news"}},
                },
                {
                    "channel": "telegram",
                    "enabled": False,
                    "max_targets": 1,
                    "targets": ["123456789"],
                },
            ],
        )
        self.assertEqual(stored, 2)

        global_stored = self.database.upsert_news_delivery_policies(
            user_id="user-1",
            topic=None,
            channels=[
                {
                    "channel": "email",
                    "enabled": True,
                    "max_targets": 1,
                    "targets": ["digest@example.com"],
                }
            ],
        )
        self.assertEqual(global_stored, 1)

        topic_rows = self.database.list_news_delivery_policies(user_id="user-1", topic="AI", include_global=True)
        self.assertEqual(len(topic_rows), 3)
        self.assertEqual(str(topic_rows[0].get("topic")), "AI")
        self.assertEqual(str(topic_rows[0].get("channel")), "telegram")
        self.assertFalse(bool(topic_rows[0].get("is_enabled")))

        webhook = next(item for item in topic_rows if str(item.get("channel")) == "webhook")
        self.assertEqual(int(webhook.get("max_targets")), 2)
        self.assertEqual(len(webhook.get("targets") or []), 3)
        self.assertEqual(str((webhook.get("options") or {}).get("headers", {}).get("X-Test")), "news")

        strict_topic_rows = self.database.list_news_delivery_policies(user_id="user-1", topic="AI", include_global=False)
        self.assertEqual(len(strict_topic_rows), 2)
        self.assertTrue(all(str(item.get("topic")) == "AI" for item in strict_topic_rows))

    def test_add_and_list_news_delivery_events(self) -> None:
        inserted = self.database.add_news_delivery_events(
            user_id="user-1",
            topic="AI",
            events=[
                {
                    "channel": "webhook",
                    "target": "https://example.com/hook",
                    "status": "delivered_dry_run",
                    "detail": "ok",
                    "digest_hash": "abc123",
                    "metadata": {"dry_run": True},
                },
                {
                    "channel": "email",
                    "target": "digest@example.com",
                    "status": "failed_runtime_error",
                    "detail": "smtp unavailable",
                    "digest_hash": "abc123",
                    "metadata": {"dry_run": False},
                },
            ],
        )
        self.assertEqual(inserted, 2)

        listed = self.database.list_news_delivery_events(user_id="user-1", topic="AI", limit=20)
        self.assertEqual(len(listed), 2)
        channels = {str(item.get("channel")) for item in listed}
        self.assertEqual(channels, {"webhook", "email"})
        dry_run_event = next(item for item in listed if str(item.get("channel")) == "webhook")
        self.assertEqual(str((dry_run_event.get("metadata") or {}).get("dry_run")).lower(), "true")


if __name__ == "__main__":
    unittest.main()
