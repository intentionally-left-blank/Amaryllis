from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MissionReportPackGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "mission_report_pack_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_gate_passes_for_valid_release_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-gate-test-") as tmp:
            base = Path(tmp)
            report = base / "mission-report.json"
            output = base / "mission-report-pack-gate-report.json"
            self._write_json(
                report,
                {
                    "suite": "mission_success_recovery_report_pack_v2",
                    "schema_version": 2,
                    "scope": "release",
                    "kpis": {
                        "mission_success_rate_pct": 100.0,
                        "recovery_pass_rate_pct": 100.0,
                        "release_quality_score_pct": 100.0,
                        "distribution_score_pct": 100.0,
                        "journey_success_rate_pct": 100.0,
                        "journey_plan_to_execute_conversion_rate_pct": 100.0,
                        "adoption_trend_gate_passed": True,
                    },
                    "class_order": [
                        "mission_execution",
                        "recovery",
                        "quality",
                        "runtime_qos",
                        "distribution",
                        "user_flow",
                        "adoption_growth",
                    ],
                    "class_breakdown": {
                        "mission_execution": {"checks_total": 2, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                        "recovery": {"checks_total": 1, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                        "quality": {"checks_total": 1, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                        "runtime_qos": {"checks_total": 1, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                        "distribution": {"checks_total": 1, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                        "user_flow": {"checks_total": 2, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                        "adoption_growth": {"checks_total": 1, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                    },
                    "summary": {
                        "checks_total": 9,
                        "checks_passed": 9,
                        "checks_failed": 0,
                        "status": "pass",
                    },
                },
            )

            proc = self._run(
                "--report",
                str(report),
                "--expected-scope",
                "release",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[mission-report-pack-gate] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "mission_report_pack_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")

    def test_gate_fails_when_required_class_or_kpi_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-gate-test-") as tmp:
            base = Path(tmp)
            report = base / "mission-report.json"
            output = base / "mission-report-pack-gate-report.json"
            self._write_json(
                report,
                {
                    "suite": "mission_success_recovery_report_pack_v2",
                    "schema_version": 2,
                    "scope": "release",
                    "kpis": {
                        "mission_success_rate_pct": 100.0,
                    },
                    "class_order": ["mission_execution", "recovery"],
                    "class_breakdown": {
                        "mission_execution": {"checks_total": 1, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                        "recovery": {"checks_total": 1, "checks_failed": 0, "status": "pass", "kpis": {"a": 1}},
                    },
                    "summary": {
                        "checks_total": 2,
                        "checks_passed": 2,
                        "checks_failed": 0,
                        "status": "pass",
                    },
                },
            )

            proc = self._run(
                "--report",
                str(report),
                "--expected-scope",
                "release",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[mission-report-pack-gate] FAILED", proc.stdout)
            self.assertIn("class_order_required", proc.stdout)
            self.assertIn("required_kpis_present", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("summary", {}).get("status")), "fail")

    def test_gate_passes_for_valid_nightly_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-gate-test-") as tmp:
            base = Path(tmp)
            report = base / "nightly-report.json"
            output = base / "nightly-mission-report-pack-gate-report.json"
            self._write_json(
                report,
                {
                    "suite": "mission_success_recovery_report_pack_v2",
                    "schema_version": 2,
                    "scope": "nightly",
                    "kpis": {
                        "nightly_success_rate_pct": 100.0,
                        "nightly_burn_rate_gate_passed": True,
                        "nightly_breaker_soak_gate_passed": True,
                        "nightly_adoption_trend_gate_passed": True,
                        "journey_success_rate_pct": 100.0,
                        "journey_plan_to_execute_conversion_rate_pct": 100.0,
                    },
                    "class_order": ["nightly_reliability", "user_flow", "adoption_growth"],
                    "class_breakdown": {
                        "nightly_reliability": {
                            "checks_total": 4,
                            "checks_failed": 0,
                            "status": "pass",
                            "kpis": {"nightly_success_rate_pct": 100.0},
                        },
                        "user_flow": {
                            "checks_total": 2,
                            "checks_failed": 0,
                            "status": "pass",
                            "kpis": {"journey_success_rate_pct": 100.0},
                        },
                        "adoption_growth": {
                            "checks_total": 2,
                            "checks_failed": 0,
                            "status": "pass",
                            "kpis": {"nightly_adoption_trend_gate_passed": True},
                        },
                    },
                    "summary": {
                        "checks_total": 8,
                        "checks_passed": 8,
                        "checks_failed": 0,
                        "status": "pass",
                    },
                },
            )

            proc = self._run(
                "--report",
                str(report),
                "--expected-scope",
                "nightly",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[mission-report-pack-gate] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")


if __name__ == "__main__":
    unittest.main()
