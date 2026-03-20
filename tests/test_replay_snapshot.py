from __future__ import annotations

import json
from pathlib import Path
import unittest

from eval.replay_snapshot import canonicalize_replay_snapshot


class ReplaySnapshotTests(unittest.TestCase):
    def test_canonicalize_replay_snapshot_matches_fixture(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        input_path = repo_root / "eval" / "fixtures" / "replay" / "sample_replay_input.json"
        expected_path = repo_root / "eval" / "fixtures" / "replay" / "sample_replay_snapshot.json"

        payload = json.loads(input_path.read_text(encoding="utf-8"))
        expected = json.loads(expected_path.read_text(encoding="utf-8"))

        actual = canonicalize_replay_snapshot(payload)
        self.assertEqual(actual, expected)

    def test_canonicalize_normalizes_fields(self) -> None:
        payload = {
            "status": "FAILED",
            "stop_reason": "MAX_ATTEMPTS_EXHAUSTED",
            "failure_class": "Timeout",
            "attempts": "2",
            "max_attempts": "2",
            "timeline": [
                {
                    "stage": "ERROR",
                    "attempt": "1",
                    "status": "FAILED",
                    "retryable": 1,
                    "failure_class": "Timeout",
                    "message": "x",
                }
            ],
            "attempt_summary": [
                {
                    "attempt": "1",
                    "stage_counts": {"ERROR": "1"},
                    "errors": ["boom"],
                }
            ],
            "resume_snapshots": [
                {
                    "attempt": "1",
                    "completed_steps": ["verify", "plan"],
                }
            ],
            "issue_summary": {
                "status_breakdown": {"Blocked": "1"},
                "tool_call_status_breakdown": {"FAILED": "2"},
            },
        }

        actual = canonicalize_replay_snapshot(payload)
        self.assertEqual(actual.get("status"), "failed")
        self.assertEqual(actual.get("failure_class"), "timeout")

        timeline = actual.get("timeline")
        self.assertIsInstance(timeline, list)
        assert isinstance(timeline, list)
        self.assertEqual(timeline[0].get("seq"), 1)
        self.assertEqual(timeline[0].get("stage"), "error")

        attempts = actual.get("attempt_summary")
        self.assertIsInstance(attempts, list)
        assert isinstance(attempts, list)
        self.assertEqual(attempts[0].get("stage_counts"), {"error": 1})

        resumes = actual.get("resume_snapshots")
        self.assertIsInstance(resumes, list)
        assert isinstance(resumes, list)
        self.assertEqual(resumes[0].get("completed_steps"), ["plan", "verify"])


if __name__ == "__main__":
    unittest.main()
