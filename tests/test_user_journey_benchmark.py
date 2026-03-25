from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class UserJourneyBenchmarkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "user_journey_benchmark.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_benchmark_report_is_generated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-user-journey-benchmark-") as tmp:
            base = Path(tmp)
            output = base / "journey-report.json"
            baseline = base / "baseline.json"
            self._write_json(
                baseline,
                {
                    "suite": "user_journey_benchmark_baseline_v1",
                    "summary": {
                        "journey_success_rate_pct": 100.0,
                        "p95_journey_latency_ms": 3000.0,
                        "p95_plan_dispatch_latency_ms": 1200.0,
                        "p95_execute_dispatch_latency_ms": 1200.0,
                        "plan_to_execute_conversion_rate_pct": 100.0,
                        "activation_success_rate_pct": 100.0,
                        "activation_blocked_rate_pct": 0.0,
                        "p95_activation_latency_ms": 600000.0,
                        "install_success_rate_pct": 100.0,
                        "retention_proxy_success_rate_pct": 100.0,
                        "feature_adoption_rate_pct": 100.0,
                    },
                },
            )

            proc = self._run(
                "--iterations",
                "1",
                "--min-success-rate-pct",
                "100",
                "--max-p95-journey-latency-ms",
                "15000",
                "--max-p95-plan-dispatch-latency-ms",
                "15000",
                "--max-p95-execute-dispatch-latency-ms",
                "15000",
                "--min-plan-to-execute-conversion-rate-pct",
                "100",
                "--baseline",
                str(baseline),
                "--output",
                str(output),
                "--strict",
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[user-journey-benchmark] OK", proc.stdout)
            self.assertTrue(output.exists())

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("suite"), "user_journey_benchmark_v1")
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status")), "pass")
            self.assertEqual(int(summary.get("journeys_total", 0)), 1)
            self.assertGreaterEqual(int(summary.get("checks_total", 0)), 1)
            self.assertIn("activation_success_rate_pct", summary)
            self.assertIn("activation_blocked_rate_pct", summary)
            self.assertIn("p95_activation_latency_ms", summary)
            self.assertIn("install_success_rate_pct", summary)
            self.assertIn("retention_proxy_success_rate_pct", summary)
            self.assertIn("feature_adoption_rate_pct", summary)

    def test_strict_mode_fails_when_threshold_is_impossible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-user-journey-benchmark-") as tmp:
            base = Path(tmp)
            output = base / "journey-report.json"
            proc = self._run(
                "--iterations",
                "1",
                "--min-success-rate-pct",
                "100",
                "--max-p95-journey-latency-ms",
                "0",
                "--max-p95-plan-dispatch-latency-ms",
                "15000",
                "--max-p95-execute-dispatch-latency-ms",
                "15000",
                "--min-plan-to-execute-conversion-rate-pct",
                "100",
                "--output",
                str(output),
                "--strict",
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[user-journey-benchmark] FAILED", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("summary", {}).get("status")), "fail")


if __name__ == "__main__":
    unittest.main()
