from __future__ import annotations

import unittest

from news.digest import compose_grounded_digest


class NewsDigestTests(unittest.TestCase):
    def test_compose_grounded_digest_keeps_cross_source_citations(self) -> None:
        digest = compose_grounded_digest(
            topic="AI",
            items=[
                {
                    "source": "web",
                    "canonical_id": "web-1",
                    "canonical_story_key": "https://example.com/story",
                    "url": "https://example.com/story",
                    "title": "Major AI launch",
                    "excerpt": "Launch details and ecosystem impact.",
                    "published_at": "2026-04-05T10:00:00+00:00",
                    "raw_score": 0.91,
                    "metadata": {
                        "provenance": [
                            {
                                "source": "web",
                                "canonical_id": "web-1",
                                "canonical_story_key": "https://example.com/story",
                                "url": "https://example.com/story",
                                "title": "Major AI launch",
                                "published_at": "2026-04-05T10:00:00+00:00",
                            },
                            {
                                "source": "reddit",
                                "canonical_id": "t3_abc",
                                "canonical_story_key": "https://example.com/story",
                                "url": "https://example.com/story",
                                "title": "Discussion thread",
                                "published_at": "2026-04-05T10:30:00+00:00",
                            },
                        ]
                    },
                }
            ],
        )

        self.assertEqual(digest.get("topic"), "AI")
        self.assertEqual(digest.get("metrics", {}).get("section_count"), 1)
        self.assertEqual(digest.get("metrics", {}).get("citation_coverage_rate"), 1.0)
        section = digest.get("sections", [])[0]
        self.assertEqual(section.get("confidence"), "high")
        self.assertEqual(section.get("source_count"), 2)
        self.assertEqual(section.get("provenance_count"), 2)
        self.assertEqual(
            {str(ref.get("source")) for ref in section.get("source_refs", [])},
            {"web", "reddit"},
        )

    def test_compose_grounded_digest_uses_fallback_citation_when_provenance_missing(self) -> None:
        digest = compose_grounded_digest(
            topic="AI",
            items=[
                {
                    "source": "web",
                    "canonical_id": "web-1",
                    "url": "https://example.com/one",
                    "title": "First",
                    "published_at": "2026-04-05T09:00:00+00:00",
                    "raw_score": 0.4,
                    "metadata": {},
                },
                {
                    "source": "web",
                    "canonical_id": "web-2",
                    "url": "https://example.com/two",
                    "title": "Second",
                    "published_at": "2026-04-05T08:00:00+00:00",
                    "raw_score": 0.2,
                    "metadata": {},
                },
            ],
            max_sections=1,
        )

        self.assertEqual(digest.get("metrics", {}).get("section_count"), 1)
        section = digest.get("sections", [])[0]
        self.assertEqual(section.get("headline"), "First")
        self.assertEqual(section.get("confidence"), "medium")
        refs = section.get("source_refs", [])
        self.assertEqual(len(refs), 1)
        self.assertEqual(str(refs[0].get("url")), "https://example.com/one")

    def test_compose_grounded_digest_handles_empty_input(self) -> None:
        digest = compose_grounded_digest(topic="AI", items=[])
        self.assertEqual(digest.get("sections"), [])
        self.assertEqual(digest.get("top_links"), [])
        self.assertEqual(digest.get("metrics", {}).get("citation_coverage_rate"), 1.0)
        self.assertEqual(
            digest.get("citation_policy", {}).get("version"),
            "digest_citation_policy_v1",
        )


if __name__ == "__main__":
    unittest.main()

