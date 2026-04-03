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
        self.assertEqual(report.get("per_source_count", {}).get("web"), 2)

        items = report.get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(str(items[0].get("url")), "https://openai.com/blog/launch?utm_source=test")

        first_call = registry.calls[0]
        metadata = first_call.get("metadata")
        self.assertIsInstance(metadata, dict)
        include_domains = metadata.get("include_domains")
        self.assertIn("openai.com", include_domains)


if __name__ == "__main__":
    unittest.main()
