from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentFactoryPlanPerfBaselineRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "agent_factory_plan_perf_baseline_refresh.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_refresh_report_and_suggested_baseline_are_generated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-baseline-refresh-") as tmp:
            tmp_dir = Path(tmp)
            baseline = tmp_dir / "baseline.json"
            release_report = tmp_dir / "release-report.json"
            nightly_report = tmp_dir / "nightly-report.json"
            output = tmp_dir / "refresh-report.json"
            suggested = tmp_dir / "suggested-baseline.json"

            baseline.write_text(
                json.dumps(
                    {
                        "suite": "agent_factory_plan_perf_envelope_v1",
                        "profiles": {
                            "release": {
                                "requests_total": 10,
                                "concurrency": 2,
                                "max_p95_latency_ms": 1200.0,
                                "max_error_rate_pct": 0.0,
                            },
                            "nightly": {
                                "requests_total": 12,
                                "concurrency": 3,
                                "max_p95_latency_ms": 1500.0,
                                "max_error_rate_pct": 0.0,
                            },
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            release_report.write_text(
                json.dumps(
                    {
                        "summary": {
                            "status": "pass",
                            "p95_latency_ms": 1000.0,
                            "error_rate_pct": 0.0,
                        },
                        "thresholds": {
                            "max_p95_latency_ms": 1200.0,
                            "max_error_rate_pct": 0.0,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            nightly_report.write_text(
                json.dumps(
                    {
                        "summary": {
                            "status": "pass",
                            "p95_latency_ms": 1100.0,
                            "error_rate_pct": 0.0,
                        },
                        "thresholds": {
                            "max_p95_latency_ms": 1500.0,
                            "max_error_rate_pct": 0.0,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            proc = self._run(
                "--baseline",
                str(baseline),
                "--report",
                f"release={release_report}",
                "--report",
                f"nightly={nightly_report}",
                "--output",
                str(output),
                "--write-updated-baseline",
                str(suggested),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[agent-factory-plan-perf-baseline-refresh] OK", proc.stdout)
            self.assertTrue(output.exists())
            self.assertTrue(suggested.exists())

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite") or ""), "agent_factory_plan_perf_baseline_refresh_v1")
            summary = payload.get("summary", {})
            self.assertIsInstance(summary, dict)
            self.assertEqual(str(summary.get("status") or ""), "pass")
            profiles = payload.get("profiles", [])
            self.assertIsInstance(profiles, list)
            self.assertEqual(len(profiles), 2)

            suggested_payload = json.loads(suggested.read_text(encoding="utf-8"))
            profiles_payload = suggested_payload.get("profiles", {})
            self.assertIsInstance(profiles_payload, dict)
            self.assertIn("release", profiles_payload)
            self.assertIn("nightly", profiles_payload)

    def test_strict_mode_fails_when_error_rate_exceeds_baseline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-baseline-refresh-") as tmp:
            tmp_dir = Path(tmp)
            baseline = tmp_dir / "baseline.json"
            release_report = tmp_dir / "release-report.json"
            output = tmp_dir / "refresh-report.json"

            baseline.write_text(
                json.dumps(
                    {
                        "suite": "agent_factory_plan_perf_envelope_v1",
                        "profiles": {
                            "release": {
                                "requests_total": 10,
                                "concurrency": 2,
                                "max_p95_latency_ms": 1200.0,
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
            release_report.write_text(
                json.dumps(
                    {
                        "summary": {
                            "status": "pass",
                            "p95_latency_ms": 800.0,
                            "error_rate_pct": 3.5,
                        },
                        "thresholds": {
                            "max_p95_latency_ms": 1200.0,
                            "max_error_rate_pct": 0.0,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            proc = self._run(
                "--baseline",
                str(baseline),
                "--report",
                f"release={release_report}",
                "--output",
                str(output),
                "--strict",
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[agent-factory-plan-perf-baseline-refresh] FAILED", proc.stdout)

    def test_input_validation_for_missing_report_mapping(self) -> None:
        proc = self._run("--baseline", "eval/baselines/quality/agent_factory_plan_perf_envelope.json")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("at least one --report", proc.stderr)


if __name__ == "__main__":
    unittest.main()
