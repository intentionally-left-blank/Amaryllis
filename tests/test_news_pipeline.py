from __future__ import annotations

import unittest

from news.pipeline import NewsIngestionPipeline, build_query_bundle


class _FakeRegistry:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(self, *, source: str, query):  # noqa: ANN001
        self.calls.append(
            {
                "source": source,
                "query": query.query,
                "metadata": dict(query.metadata),
            }
        )
        return [
            {
                "source": "web",
                "canonical_id": "one",
                "url": "https://openai.com/blog/launch?utm_source=test",
                "title": "Launch",
                "published_at": "2026-04-04T00:00:00+00:00",
                "metadata": {},
            },
            {
                "source": "web",
                "canonical_id": "two",
                "url": "https://openai.com/blog/launch",
                "title": "Launch",
                "published_at": "2026-04-04T00:00:00+00:00",
                "metadata": {},
            },
            {
                "source": "web",
                "canonical_id": "three",
                "url": "https://example.org/post",
                "title": "Other",
                "published_at": "2026-04-04T00:10:00+00:00",
                "metadata": {},
            },
        ]


class _CrossSourceRegistry:
    def search(self, *, source: str, query):  # noqa: ANN001
        _ = query
        if source == "web":
            return [
                {
                    "source": "web",
                    "canonical_id": "web-one",
                    "url": "https://example.com/story",
                    "title": "Story",
                    "published_at": "2026-04-04T00:00:00+00:00",
                    "metadata": {"connector": "web"},
                }
            ]
        if source == "reddit":
            return [
                {
                    "source": "reddit",
                    "canonical_id": "reddit-one",
                    "url": "https://example.com/story",
                    "title": "Story from Reddit",
                    "published_at": "2026-04-04T00:10:00+00:00",
                    "metadata": {"connector": "reddit", "subreddit": "MachineLearning"},
                }
            ]
        return []


class NewsPipelineTests(unittest.TestCase):
    def test_build_query_bundle_uses_seed_urls_and_depth(self) -> None:
        bundle = build_query_bundle(
            topic="AI",
            queries=["ai agents"],
            include_domains=["arxiv.org"],
            seed_urls=["https://news.ycombinator.com/newest", "openai.com/blog/research"],
            max_depth=2,
        )
        self.assertTrue(any("site:arxiv.org" in item for item in bundle))
        self.assertTrue(any("site:news.ycombinator.com" in item for item in bundle))
        self.assertTrue(any("site:openai.com" in item for item in bundle))
        self.assertTrue(any("blog" in item.lower() for item in bundle))

    def test_ingest_preview_filters_domains_and_deduplicates(self) -> None:
        registry = _FakeRegistry()
        pipeline = NewsIngestionPipeline(source_registry=registry)  # type: ignore[arg-type]

        report = pipeline.ingest_preview(
            topic="AI",
            sources=["web"],
            window_hours=24,
            max_items_per_source=20,
            internet_scope={
                "queries": ["ai agents"],
                "seed_urls": ["openai.com/blog/research"],
                "exclude_domains": ["example.org"],
                "max_depth": 2,
            },
        )

        self.assertEqual(report.get("raw_count"), 2)
        self.assertEqual(report.get("deduped_count"), 1)
        self.assertEqual(report.get("duplicate_count"), 1)
        self.assertEqual(report.get("per_source_count", {}).get("web"), 2)
        self.assertEqual(
            report.get("dedup_policy"),
            {"strategy": "canonical_url_key_v1", "unique_story_count": 1},
        )

        items = report.get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(str(items[0].get("url")), "https://openai.com/blog/launch?utm_source=test")
        self.assertEqual(str(items[0].get("canonical_story_key")), "https://openai.com/blog/launch")
        metadata = items[0].get("metadata")
        self.assertIsInstance(metadata, dict)
        self.assertEqual(str(metadata.get("canonical_story_key")), "https://openai.com/blog/launch")
        self.assertEqual(
            metadata.get("dedup_policy"),
            {
                "strategy": "canonical_url_key_v1",
                "key": "https://openai.com/blog/launch",
            },
        )
        provenance = metadata.get("provenance")
        self.assertIsInstance(provenance, list)
        self.assertEqual(len(provenance), 2)
        self.assertEqual(
            {str(item.get("canonical_story_key")) for item in provenance},
            {"https://openai.com/blog/launch"},
        )
        merged_sources = metadata.get("merged_sources")
        self.assertEqual(merged_sources, ["web"])
        self.assertEqual(metadata.get("merged_count"), 2)

        first_call = registry.calls[0]
        call_metadata = first_call.get("metadata")
        self.assertIsInstance(call_metadata, dict)
        include_domains = call_metadata.get("include_domains")
        self.assertIn("openai.com", include_domains)

    def test_ingest_preview_keeps_cross_source_provenance_for_same_story(self) -> None:
        registry = _CrossSourceRegistry()
        pipeline = NewsIngestionPipeline(source_registry=registry)  # type: ignore[arg-type]

        report = pipeline.ingest_preview(
            topic="AI",
            sources=["web", "reddit"],
            window_hours=24,
            max_items_per_source=10,
            internet_scope={},
        )

        self.assertEqual(report.get("raw_count"), 2)
        self.assertEqual(report.get("deduped_count"), 1)
        self.assertEqual(report.get("duplicate_count"), 1)
        self.assertEqual(
            report.get("dedup_policy"),
            {"strategy": "canonical_url_key_v1", "unique_story_count": 1},
        )
        items = report.get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(str(items[0].get("canonical_story_key")), "https://example.com/story")
        metadata = items[0].get("metadata")
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata.get("merged_sources"), ["web", "reddit"])
        self.assertEqual(metadata.get("merged_count"), 2)
        self.assertEqual(str(metadata.get("canonical_story_key")), "https://example.com/story")
        provenance = metadata.get("provenance")
        self.assertIsInstance(provenance, list)
        self.assertEqual(len(provenance), 2)
        sources = {str(item.get("source")) for item in provenance}
        self.assertEqual(sources, {"web", "reddit"})


if __name__ == "__main__":
    unittest.main()
