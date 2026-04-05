from __future__ import annotations

import json
from pathlib import Path
import unittest

from eval.news_digest_snapshot import canonicalize_news_digest_snapshot
from news.digest import compose_grounded_digest


class NewsDigestReplaySnapshotTests(unittest.TestCase):
    def test_news_digest_snapshot_matches_fixture(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        input_path = repo_root / "eval" / "fixtures" / "replay" / "news" / "news_digest_input.json"
        expected_path = repo_root / "eval" / "fixtures" / "replay" / "news" / "news_digest_snapshot.json"

        payload = json.loads(input_path.read_text(encoding="utf-8"))
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        digest = compose_grounded_digest(
            topic=str(payload.get("topic") or ""),
            items=list(payload.get("items") or []),
            max_sections=int(payload.get("max_sections") or 5),
        )
        actual = canonicalize_news_digest_snapshot(digest)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()

