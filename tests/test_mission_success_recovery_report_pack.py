from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MissionSuccessRecoveryReportPackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "build_mission_success_recovery_report.py"

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

    def test_release_report_pack_is_generated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-") as tmp:
            base = Path(tmp)
            mission = base / "mission.json"
            fault = base / "fault.json"
            quality = base / "quality.json"
            distribution = base / "distribution.json"
            macos_staging = base / "macos-staging.json"
            journey = base / "journey.json"
            adoption_trend = base / "adoption-trend.json"
            breaker_gate = base / "breaker-gate.json"
            output = base / "report.json"

            self._write_json(
                mission,
                {
                    "suite": "mission_queue_load_gate_v1",
                    "config": {
                        "min_success_rate_pct": 99.0,
                        "max_failed_runs": 0,
                        "max_p95_queue_wait_ms": 1500.0,
                        "max_p95_end_to_end_ms": 5000.0,
                    },
                    "summary": {
                        "success_rate_pct": 100.0,
                        "failed_or_canceled": 0,
                        "p95_queue_wait_ms": 800.0,
                        "p95_end_to_end_ms": 1900.0,
                    },
                },
            )
            self._write_json(
                fault,
                {
                    "suite": "fault_injection_reliability_v1",
                    "summary": {
                        "pass_rate_pct": 100.0,
                        "min_pass_rate_pct": 100.0,
                    },
                },
            )
            self._write_json(
                quality,
                {
                    "suite": "release_quality_dashboard_v1",
                    "summary": {
                        "quality_score_pct": 100.0,
                        "status": "pass",
                    },
                    "signals": [
                        {"metric_id": "qos_governor.status", "value": 1.0},
                        {"metric_id": "qos_governor.checks_failed", "value": 0.0},
                    ],
                },
            )
            self._write_json(
                distribution,
                {
                    "suite": "distribution_resilience_report_v1",
                    "summary": {
                        "checks_total": 14,
                        "checks_passed": 14,
                        "checks_failed": 0,
                        "score_pct": 100.0,
                        "status": "pass",
                    },
                },
            )
            self._write_json(
                journey,
                {
                    "suite": "user_journey_benchmark_v1",
                    "config": {
                        "thresholds": {
                            "min_success_rate_pct": 100.0,
                            "max_p95_journey_latency_ms": 3000.0,
                            "max_p95_plan_dispatch_latency_ms": 1200.0,
                            "max_p95_execute_dispatch_latency_ms": 1200.0,
                            "min_plan_to_execute_conversion_rate_pct": 100.0,
                            "min_activation_success_rate_pct": 100.0,
                            "max_blocked_activation_rate_pct": 0.0,
                            "max_p95_activation_latency_ms": 10000.0,
                            "min_install_success_rate_pct": 100.0,
                            "min_retention_proxy_success_rate_pct": 100.0,
                            "min_feature_adoption_rate_pct": 100.0,
                        }
                    },
                    "summary": {
                        "journey_success_rate_pct": 100.0,
                        "p95_journey_latency_ms": 800.0,
                        "p95_plan_dispatch_latency_ms": 300.0,
                        "p95_execute_dispatch_latency_ms": 350.0,
                        "plan_to_execute_conversion_rate_pct": 100.0,
                        "activation_success_rate_pct": 100.0,
                        "activation_blocked_rate_pct": 0.0,
                        "p95_activation_latency_ms": 600.0,
                        "install_success_rate_pct": 100.0,
                        "retention_proxy_success_rate_pct": 100.0,
                        "feature_adoption_rate_pct": 100.0,
                    },
                },
            )
            self._write_json(
                macos_staging,
                {
                    "suite": "macos_desktop_parity_smoke_v1",
                    "summary": {
                        "checks_total": 8,
                        "checks_passed": 8,
                        "checks_failed": 0,
                        "error_rate_pct": 0.0,
                        "status": "pass",
                        "latency_ms": {"p50": 1.0, "p95": 2.0, "max": 3.0},
                    },
                },
            )
            self._write_json(
                adoption_trend,
                {
                    "suite": "adoption_kpi_trend_gate_v1",
                    "summary": {
                        "status": "pass",
                        "checks_total": 9,
                        "checks_passed": 9,
                        "checks_failed": 0,
                        "compared_metrics": 7,
                        "improved": 0,
                        "regressed": 0,
                        "unchanged": 7,
                    },
                },
            )
            self._write_json(
                breaker_gate,
                {
                    "suite": "autonomy_circuit_breaker_gate_v1",
                    "summary": {
                        "status": "pass",
                        "total": 24,
                        "passed": 24,
                        "failed": 0,
                    },
                    "checks": [
                        {"name": "runtime_domains_endpoint_ok", "ok": True},
                        {"name": "runtime_domains_contract_contains_three_domains", "ok": True},
                        {"name": "runtime_domains_reports_blocked_counts", "ok": True},
                        {"name": "runtime_domains_summary_includes_all_blocked_domains", "ok": True},
                    ],
                },
            )

            proc = self._run(
                "--mission-queue-report",
                str(mission),
                "--fault-injection-report",
                str(fault),
                "--quality-dashboard-report",
                str(quality),
                "--distribution-resilience-report",
                str(distribution),
                "--macos-desktop-parity-report",
                str(macos_staging),
                "--user-journey-report",
                str(journey),
                "--adoption-kpi-trend-report",
                str(adoption_trend),
                "--breaker-gate-report",
                str(breaker_gate),
                "--scope",
                "release",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[mission-report-pack] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "mission_success_recovery_report_pack_v2")
            self.assertEqual(int(payload.get("schema_version", 0)), 2)
            self.assertEqual(payload.get("scope"), "release")
            self.assertEqual(payload.get("summary", {}).get("status"), "pass")
            self.assertGreaterEqual(int(payload.get("summary", {}).get("checks_total", 0)), 1)
            self.assertIn("journey_success_rate_pct", payload.get("kpis", {}))
            self.assertIn("journey.plan_to_execute_conversion_rate_pct", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("journey.activation_success_rate_pct", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("journey.install_success_rate_pct", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("journey.retention_proxy_success_rate_pct", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("journey.feature_adoption_rate_pct", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("qos_governor.status", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("adoption_trend.status", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("autonomy.breaker_gate_passed", [c.get("id") for c in payload.get("checks", [])])
            self.assertIn("autonomy.breaker_domains_contract_passed", [c.get("id") for c in payload.get("checks", [])])
            class_breakdown = payload.get("class_breakdown", {})
            self.assertEqual(str(class_breakdown.get("mission_execution", {}).get("status")), "pass")
            self.assertEqual(str(class_breakdown.get("distribution", {}).get("status")), "pass")
            self.assertEqual(str(class_breakdown.get("desktop_staging", {}).get("status")), "pass")
            self.assertEqual(str(class_breakdown.get("runtime_qos", {}).get("status")), "pass")
            self.assertEqual(str(class_breakdown.get("adoption_growth", {}).get("status")), "pass")
            self.assertIn("autonomy_breaker_gate_passed", payload.get("kpis", {}))
            self.assertIn("autonomy_breaker_domains_contract_passed", payload.get("kpis", {}))
            self.assertIn("distribution_score_pct", class_breakdown.get("distribution", {}).get("kpis", {}))
            self.assertIn(
                "desktop_staging_error_rate_pct",
                class_breakdown.get("desktop_staging", {}).get("kpis", {}),
            )
            self.assertIn("journey_success_rate_pct", class_breakdown.get("user_flow", {}).get("kpis", {}))
            self.assertIn("journey_activation_success_rate_pct", class_breakdown.get("user_flow", {}).get("kpis", {}))
            self.assertIn("journey_install_success_rate_pct", class_breakdown.get("user_flow", {}).get("kpis", {}))
            self.assertIn(
                "journey_retention_proxy_success_rate_pct",
                class_breakdown.get("user_flow", {}).get("kpis", {}),
            )
            self.assertIn("journey_feature_adoption_rate_pct", class_breakdown.get("user_flow", {}).get("kpis", {}))
            self.assertIn("qos_gate_checks_failed", class_breakdown.get("runtime_qos", {}).get("kpis", {}))
            self.assertIn("adoption_trend_checks_failed", class_breakdown.get("adoption_growth", {}).get("kpis", {}))

    def test_nightly_report_pack_marks_failed_summary_when_burn_gate_failed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-") as tmp:
            base = Path(tmp)
            nightly = base / "nightly.json"
            burn = base / "burn.json"
            adoption_trend = base / "adoption-trend.json"
            breaker_soak = base / "breaker-soak.json"
            breaker_gate = base / "breaker-gate.json"
            output = base / "report.json"

            self._write_json(
                nightly,
                {
                    "suite": "nightly_reliability_smoke_v1",
                    "thresholds": {
                        "min_success_rate_pct": 99.0,
                        "max_p95_latency_ms": 600.0,
                        "max_latency_jitter_ms": 120.0,
                    },
                    "summary": {
                        "success_rate_pct": 99.5,
                        "p95_latency_ms": 400.0,
                        "latency_jitter_ms": 60.0,
                    },
                },
            )
            self._write_json(
                burn,
                {
                    "suite": "nightly_slo_burn_rate_gate_v1",
                    "passed": False,
                    "summary": {
                        "request": {"max_consecutive_breach_samples": 3},
                        "runs": {"max_consecutive_breach_samples": 2},
                    },
                },
            )
            self._write_json(
                adoption_trend,
                {
                    "suite": "adoption_kpi_trend_gate_v1",
                    "summary": {
                        "status": "pass",
                        "checks_total": 9,
                        "checks_passed": 9,
                        "checks_failed": 0,
                        "compared_metrics": 7,
                        "improved": 0,
                        "regressed": 0,
                        "unchanged": 7,
                    },
                },
            )
            self._write_json(
                breaker_soak,
                {
                    "suite": "autonomy_circuit_breaker_soak_gate_v1",
                    "config": {
                        "cycles": 3,
                        "min_success_rate_pct": 100.0,
                        "max_failed_cycles": 0,
                        "max_p95_cycle_latency_ms": 4500.0,
                    },
                    "summary": {
                        "status": "pass",
                        "cycles_total": 3,
                        "cycles_passed": 3,
                        "cycles_failed": 0,
                        "success_rate_pct": 100.0,
                        "p95_cycle_latency_ms": 1500.0,
                    },
                },
            )
            self._write_json(
                breaker_gate,
                {
                    "suite": "autonomy_circuit_breaker_gate_v1",
                    "summary": {
                        "status": "pass",
                        "total": 30,
                        "passed": 30,
                        "failed": 0,
                    },
                    "checks": [
                        {"name": "runtime_domains_endpoint_ok", "ok": True},
                        {"name": "runtime_domains_contract_contains_three_domains", "ok": True},
                        {"name": "runtime_domains_reports_blocked_counts", "ok": True},
                        {"name": "runtime_domains_summary_includes_all_blocked_domains", "ok": True},
                    ],
                },
            )

            proc = self._run(
                "--nightly-reliability-report",
                str(nightly),
                "--nightly-burn-rate-report",
                str(burn),
                "--adoption-kpi-trend-report",
                str(adoption_trend),
                "--breaker-soak-report",
                str(breaker_soak),
                "--breaker-gate-report",
                str(breaker_gate),
                "--scope",
                "nightly",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "mission_success_recovery_report_pack_v2")
            self.assertEqual(payload.get("scope"), "nightly")
            self.assertEqual(payload.get("summary", {}).get("status"), "fail")
            class_breakdown = payload.get("class_breakdown", {})
            self.assertEqual(str(class_breakdown.get("nightly_reliability", {}).get("status")), "fail")
            self.assertIn("nightly_adoption_trend_gate_passed", payload.get("kpis", {}))
            self.assertIn("nightly_breaker_soak_gate_passed", payload.get("kpis", {}))
            self.assertIn("nightly_autonomy_breaker_gate_passed", payload.get("kpis", {}))
            self.assertIn("nightly_autonomy_breaker_domains_contract_passed", payload.get("kpis", {}))

    def test_missing_source_report_returns_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-") as tmp:
            base = Path(tmp)
            output = base / "report.json"
            proc = self._run(
                "--mission-queue-report",
                str(base / "missing.json"),
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("missing source report", proc.stderr.lower())

    def test_release_report_pack_marks_failed_summary_when_distribution_failed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-") as tmp:
            base = Path(tmp)
            distribution = base / "distribution.json"
            output = base / "report.json"

            self._write_json(
                distribution,
                {
                    "suite": "distribution_resilience_report_v1",
                    "summary": {
                        "checks_total": 12,
                        "checks_passed": 10,
                        "checks_failed": 2,
                        "score_pct": 83.3333,
                        "status": "fail",
                    },
                },
            )

            proc = self._run(
                "--distribution-resilience-report",
                str(distribution),
                "--scope",
                "release",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "mission_success_recovery_report_pack_v2")
            self.assertEqual(payload.get("summary", {}).get("status"), "fail")
            self.assertIn("distribution.status", [c.get("id") for c in payload.get("checks", [])])
            class_breakdown = payload.get("class_breakdown", {})
            self.assertEqual(str(class_breakdown.get("distribution", {}).get("status")), "fail")

    def test_release_report_pack_marks_failed_summary_when_macos_staging_failed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-") as tmp:
            base = Path(tmp)
            macos_staging = base / "macos-staging.json"
            output = base / "report.json"

            self._write_json(
                macos_staging,
                {
                    "suite": "macos_desktop_parity_smoke_v1",
                    "summary": {
                        "checks_total": 8,
                        "checks_passed": 5,
                        "checks_failed": 3,
                        "error_rate_pct": 37.5,
                        "status": "fail",
                        "latency_ms": {"p50": 1.0, "p95": 2.0, "max": 4.0},
                    },
                },
            )

            proc = self._run(
                "--macos-desktop-parity-report",
                str(macos_staging),
                "--scope",
                "release",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "mission_success_recovery_report_pack_v2")
            self.assertEqual(payload.get("summary", {}).get("status"), "fail")
            self.assertIn("desktop_staging.status", [c.get("id") for c in payload.get("checks", [])])
            class_breakdown = payload.get("class_breakdown", {})
            self.assertEqual(str(class_breakdown.get("desktop_staging", {}).get("status")), "fail")

    def test_release_report_pack_marks_failed_summary_when_qos_signal_failed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-pack-") as tmp:
            base = Path(tmp)
            quality = base / "quality.json"
            output = base / "report.json"

            self._write_json(
                quality,
                {
                    "suite": "release_quality_dashboard_v1",
                    "summary": {
                        "quality_score_pct": 100.0,
                        "status": "pass",
                    },
                    "signals": [
                        {"metric_id": "qos_governor.status", "value": 0.0},
                        {"metric_id": "qos_governor.checks_failed", "value": 1.0},
                    ],
                },
            )

            proc = self._run(
                "--quality-dashboard-report",
                str(quality),
                "--scope",
                "release",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("summary", {}).get("status"), "fail")
            self.assertIn("qos_governor.status", [c.get("id") for c in payload.get("checks", [])])
            class_breakdown = payload.get("class_breakdown", {})
            self.assertEqual(str(class_breakdown.get("runtime_qos", {}).get("status")), "fail")


if __name__ == "__main__":
    unittest.main()
