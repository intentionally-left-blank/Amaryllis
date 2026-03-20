from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class NightlyReliabilityRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "nightly_reliability_run.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_nightly_reliability_report_is_generated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-nightly-test-") as tmp:
            report_path = Path(tmp) / "nightly-report.json"
            baseline_path = Path(tmp) / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "summary": {
                            "success_rate_pct": 100.0,
                            "p95_latency_ms": 100.0,
                            "latency_jitter_ms": 20.0,
                            "stability_score": 80.0,
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            proc = self._run(
                "--iterations",
                "1",
                "--min-success-rate-pct",
                "100",
                "--max-p95-latency-ms",
                "10000",
                "--max-latency-jitter-ms",
                "10000",
                "--baseline",
                str(baseline_path),
                "--output",
                str(report_path),
                "--strict",
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[nightly-reliability] OK", proc.stdout)
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report.get("suite"), "nightly_reliability_smoke_v1")
            self.assertIn("summary", report)
            self.assertIn("trend_deltas", report)
            burn_rate = report.get("burn_rate")
            self.assertIsInstance(burn_rate, dict)
            self.assertIn("samples", burn_rate)
            self.assertIn("summary", burn_rate)
            self.assertGreaterEqual(int(burn_rate.get("summary", {}).get("sample_count", 0)), 1)

    def test_nightly_reliability_strict_mode_fails_on_impossible_latency(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-nightly-test-") as tmp:
            report_path = Path(tmp) / "nightly-report.json"
            proc = self._run(
                "--iterations",
                "1",
                "--min-success-rate-pct",
                "99",
                "--max-p95-latency-ms",
                "0",
                "--max-latency-jitter-ms",
                "10000",
                "--output",
                str(report_path),
                "--strict",
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[nightly-reliability] FAILED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
