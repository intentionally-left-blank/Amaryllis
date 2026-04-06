from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentFactoryPlanPerfGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "agent_factory_plan_perf_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_gate_passes_with_relaxed_thresholds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-plan-perf-gate-") as tmp:
            report_path = Path(tmp) / "report.json"
            proc = self._run(
                "--requests-total",
                "12",
                "--concurrency",
                "4",
                "--max-p95-latency-ms",
                "20000",
                "--max-error-rate-pct",
                "0",
                "--output",
                str(report_path),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[agent-factory-plan-perf-gate] OK", proc.stdout)
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(str(report.get("suite") or ""), "agent_factory_plan_perf_gate_v1")
            summary = report.get("summary", {})
            self.assertIsInstance(summary, dict)
            self.assertEqual(int(summary.get("requests_total", 0)), 12)

    def test_gate_fails_with_impossible_latency_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-plan-perf-gate-") as tmp:
            report_path = Path(tmp) / "report.json"
            proc = self._run(
                "--requests-total",
                "8",
                "--concurrency",
                "4",
                "--max-p95-latency-ms",
                "0",
                "--max-error-rate-pct",
                "100",
                "--output",
                str(report_path),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[agent-factory-plan-perf-gate] FAILED", proc.stdout)

    def test_gate_validates_requests_total(self) -> None:
        proc = self._run("--requests-total", "0")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("--requests-total must be >= 1", proc.stderr)

    def test_gate_uses_baseline_profile_defaults(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-plan-perf-gate-") as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "suite": "agent_factory_plan_perf_envelope_v1",
                        "profiles": {
                            "ci_test": {
                                "requests_total": 7,
                                "concurrency": 3,
                                "max_p95_latency_ms": 20000.0,
                                "max_error_rate_pct": 0.0,
                            }
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            report_path = Path(tmp) / "report.json"
            proc = self._run(
                "--baseline",
                str(baseline_path),
                "--baseline-profile",
                "ci_test",
                "--output",
                str(report_path),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("profile=ci_test", proc.stdout)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            gate_config = report.get("gate_config", {})
            self.assertEqual(str(gate_config.get("baseline_profile") or ""), "ci_test")
            self.assertEqual(int(gate_config.get("requests_total") or 0), 7)
            self.assertEqual(int(gate_config.get("concurrency") or 0), 3)
            thresholds = report.get("thresholds", {})
            self.assertEqual(float(thresholds.get("max_p95_latency_ms") or 0.0), 20000.0)
            self.assertEqual(float(thresholds.get("max_error_rate_pct", -1.0)), 0.0)

    def test_gate_fails_when_baseline_profile_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-plan-perf-gate-") as tmp:
            baseline_path = Path(tmp) / "baseline.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "suite": "agent_factory_plan_perf_envelope_v1",
                        "profiles": {"release": {"requests_total": 5}},
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            proc = self._run("--baseline", str(baseline_path), "--baseline-profile", "missing")
            self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("baseline profile not found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
