from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MissionQueueLoadGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "mission_queue_load_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_mission_queue_load_gate_passes_with_relaxed_thresholds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-queue-load-test-") as tmp:
            report_path = Path(tmp) / "mission-queue-load-report.json"
            proc = self._run(
                "--runs-total",
                "12",
                "--submit-concurrency",
                "4",
                "--worker-count",
                "2",
                "--task-latency-ms",
                "10",
                "--scenario-timeout-sec",
                "15",
                "--min-success-rate-pct",
                "100",
                "--max-failed-runs",
                "0",
                "--max-p95-queue-wait-ms",
                "20000",
                "--max-p95-end-to-end-ms",
                "20000",
                "--output",
                str(report_path),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[mission-queue-load] OK", proc.stdout)
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report.get("suite"), "mission_queue_load_gate_v1")
            summary = dict(report.get("summary") or {})
            self.assertEqual(int(summary.get("runs_total", 0)), 12)
            self.assertEqual(int(summary.get("failed_or_canceled", 0)), 0)

    def test_mission_queue_load_gate_fails_with_impossible_end_to_end_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-queue-load-test-") as tmp:
            report_path = Path(tmp) / "mission-queue-load-report.json"
            proc = self._run(
                "--runs-total",
                "8",
                "--submit-concurrency",
                "4",
                "--worker-count",
                "2",
                "--task-latency-ms",
                "10",
                "--scenario-timeout-sec",
                "15",
                "--min-success-rate-pct",
                "100",
                "--max-failed-runs",
                "0",
                "--max-p95-queue-wait-ms",
                "20000",
                "--max-p95-end-to-end-ms",
                "0",
                "--output",
                str(report_path),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[mission-queue-load] FAILED", proc.stdout)

    def test_mission_queue_load_gate_validates_runs_total(self) -> None:
        proc = self._run("--runs-total", "0")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("--runs-total must be >= 1", proc.stderr)


if __name__ == "__main__":
    unittest.main()
