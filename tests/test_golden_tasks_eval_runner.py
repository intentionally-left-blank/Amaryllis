from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class GoldenTasksEvalRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.runner = self.repo_root / "scripts" / "eval" / "run_golden_tasks.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.runner),
                *args,
            ],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_validate_only_accepts_valid_suite(self) -> None:
        suite = {
            "suite": "sample",
            "version": "1.0",
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Sample",
                    "category": "testing",
                    "prompt": "Say hello",
                    "expected": {
                        "min_response_chars": 1,
                        "required_keywords": ["hello"],
                        "forbidden_keywords": [],
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory(prefix="amaryllis-golden-valid-") as tmp:
            suite_path = Path(tmp) / "suite.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            proc = self._run("--tasks-file", str(suite_path), "--validate-only")

        self.assertEqual(proc.returncode, 0)
        self.assertIn("suite validation OK", proc.stdout)

    def test_validate_only_rejects_duplicate_task_ids(self) -> None:
        suite = {
            "suite": "sample",
            "version": "1.0",
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Sample A",
                    "category": "testing",
                    "prompt": "A",
                    "expected": {
                        "min_response_chars": 1,
                        "required_keywords": [],
                        "forbidden_keywords": [],
                    },
                },
                {
                    "id": "TASK-001",
                    "title": "Sample B",
                    "category": "testing",
                    "prompt": "B",
                    "expected": {
                        "min_response_chars": 1,
                        "required_keywords": [],
                        "forbidden_keywords": [],
                    },
                },
            ],
        }
        with tempfile.TemporaryDirectory(prefix="amaryllis-golden-invalid-") as tmp:
            suite_path = Path(tmp) / "suite.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            proc = self._run("--tasks-file", str(suite_path), "--validate-only")

        self.assertEqual(proc.returncode, 2)
        self.assertIn("duplicate task id", proc.stderr)

    def test_fixture_mode_snapshot_update_and_check(self) -> None:
        suite = {
            "suite": "det-suite",
            "version": "1.0",
            "tasks": [
                {
                    "id": "DET-1",
                    "title": "Bullet output",
                    "category": "testing",
                    "prompt": "Provide bullets",
                    "expected": {
                        "min_response_chars": 5,
                        "required_keywords": ["toolchain"],
                        "forbidden_keywords": [],
                        "requires_bullets": True,
                    },
                }
            ],
        }
        fixture_responses = {
            "responses": {
                "DET-1": "- toolchain check\n- dependency drift",
            }
        }
        with tempfile.TemporaryDirectory(prefix="amaryllis-golden-fixture-") as tmp:
            tmp_path = Path(tmp)
            suite_path = tmp_path / "suite.json"
            responses_path = tmp_path / "responses.json"
            report_path = tmp_path / "report.json"
            snapshot_path = tmp_path / "snapshot.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            responses_path.write_text(json.dumps(fixture_responses), encoding="utf-8")

            update_proc = self._run(
                "--tasks-file",
                str(suite_path),
                "--fixture-responses",
                str(responses_path),
                "--output",
                str(report_path),
                "--snapshot-expected",
                str(snapshot_path),
                "--update-snapshot",
                "--seed",
                "42",
            )
            self.assertEqual(update_proc.returncode, 0, msg=f"stderr={update_proc.stderr}")
            self.assertTrue(snapshot_path.exists())

            check_proc = self._run(
                "--tasks-file",
                str(suite_path),
                "--fixture-responses",
                str(responses_path),
                "--output",
                str(report_path),
                "--snapshot-expected",
                str(snapshot_path),
                "--seed",
                "42",
            )

        self.assertEqual(check_proc.returncode, 0, msg=f"stderr={check_proc.stderr}")
        self.assertIn("snapshot check OK", check_proc.stdout)

    def test_fixture_mode_detects_snapshot_drift(self) -> None:
        suite = {
            "suite": "det-suite",
            "version": "1.0",
            "tasks": [
                {
                    "id": "DET-1",
                    "title": "Code output",
                    "category": "testing",
                    "prompt": "Provide code block",
                    "expected": {
                        "min_response_chars": 5,
                        "required_keywords": ["check_toolchain_drift.py"],
                        "forbidden_keywords": [],
                        "requires_code_block": True,
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory(prefix="amaryllis-golden-fixture-drift-") as tmp:
            tmp_path = Path(tmp)
            suite_path = tmp_path / "suite.json"
            responses_path = tmp_path / "responses.json"
            report_path = tmp_path / "report.json"
            snapshot_path = tmp_path / "snapshot.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            responses_path.write_text(
                json.dumps(
                    {
                        "responses": {
                            "DET-1": textwrap.dedent(
                                """
                                ```bash
                                python scripts/release/check_toolchain_drift.py
                                ```
                                """
                            ).strip(),
                        }
                    }
                ),
                encoding="utf-8",
            )

            update_proc = self._run(
                "--tasks-file",
                str(suite_path),
                "--fixture-responses",
                str(responses_path),
                "--output",
                str(report_path),
                "--snapshot-expected",
                str(snapshot_path),
                "--update-snapshot",
                "--seed",
                "99",
            )
            self.assertEqual(update_proc.returncode, 0, msg=f"stderr={update_proc.stderr}")

            responses_path.write_text(
                json.dumps(
                    {
                        "responses": {
                            "DET-1": "toolchain check only",
                        }
                    }
                ),
                encoding="utf-8",
            )

            drift_proc = self._run(
                "--tasks-file",
                str(suite_path),
                "--fixture-responses",
                str(responses_path),
                "--output",
                str(report_path),
                "--snapshot-expected",
                str(snapshot_path),
                "--seed",
                "99",
            )

        self.assertEqual(drift_proc.returncode, 1)
        self.assertIn("snapshot drift detected", drift_proc.stderr)


if __name__ == "__main__":
    unittest.main()
