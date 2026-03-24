from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class ReleaseQualityDashboardSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "build_quality_dashboard_snapshot.py"

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

    def _write_reports(
        self,
        *,
        base: Path,
        perf_p95: float = 210.0,
        distribution_status: str = "pass",
        macos_status: str = "pass",
    ) -> dict[str, Path]:
        perf = base / "perf.json"
        fault = base / "fault.json"
        mission = base / "mission.json"
        runtime = base / "runtime.json"
        journey = base / "journey.json"
        distribution = base / "distribution.json"
        macos = base / "macos-desktop-parity.json"
        injection = base / "injection-containment.json"

        self._write_json(
            perf,
            {
                "suite": "perf_smoke_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "summary": {
                    "p95_latency_ms": perf_p95,
                    "error_rate_pct": 0.0,
                },
                "thresholds": {
                    "max_p95_latency_ms": 350.0,
                    "max_error_rate_pct": 0.0,
                },
            },
        )
        self._write_json(
            fault,
            {
                "suite": "fault_injection_reliability_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "summary": {
                    "pass_rate_pct": 100.0,
                    "min_pass_rate_pct": 100.0,
                },
            },
        )
        self._write_json(
            mission,
            {
                "suite": "mission_queue_load_gate_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "config": {
                    "min_success_rate_pct": 99.0,
                    "max_failed_runs": 0,
                    "max_p95_queue_wait_ms": 1500.0,
                    "max_p95_end_to_end_ms": 5000.0,
                },
                "summary": {
                    "success_rate_pct": 100.0,
                    "failed_or_canceled": 0,
                    "p95_queue_wait_ms": 500.0,
                    "p95_end_to_end_ms": 1600.0,
                },
            },
        )
        self._write_json(
            runtime,
            {
                "suite": "runtime_lifecycle_smoke_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "summary": {
                    "targets_ok": True,
                    "startup_ok": True,
                    "checks_failed": 0,
                },
            },
        )
        self._write_json(
            journey,
            {
                "suite": "user_journey_benchmark_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "config": {
                    "thresholds": {
                        "min_success_rate_pct": 100.0,
                        "max_p95_journey_latency_ms": 3000.0,
                        "max_p95_plan_dispatch_latency_ms": 1200.0,
                        "max_p95_execute_dispatch_latency_ms": 1200.0,
                        "min_plan_to_execute_conversion_rate_pct": 100.0,
                    }
                },
                "summary": {
                    "journey_success_rate_pct": 100.0,
                    "p95_journey_latency_ms": 900.0,
                    "p95_plan_dispatch_latency_ms": 250.0,
                    "p95_execute_dispatch_latency_ms": 300.0,
                    "plan_to_execute_conversion_rate_pct": 100.0,
                },
            },
        )
        self._write_json(
            distribution,
            {
                "suite": "distribution_resilience_report_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "summary": {
                    "checks_total": 14,
                    "checks_passed": 14 if distribution_status == "pass" else 12,
                    "checks_failed": 0 if distribution_status == "pass" else 2,
                    "score_pct": 100.0 if distribution_status == "pass" else 85.7143,
                    "status": distribution_status,
                },
            },
        )
        self._write_json(
            macos,
            {
                "suite": "macos_desktop_parity_smoke_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "summary": {
                    "checks_total": 16,
                    "checks_passed": 16 if macos_status == "pass" else 13,
                    "checks_failed": 0 if macos_status == "pass" else 3,
                    "error_rate_pct": 0.0 if macos_status == "pass" else 18.75,
                    "status": macos_status,
                },
            },
        )
        self._write_json(
            injection,
            {
                "suite": "injection_containment_gate_v1",
                "generated_at": "2026-03-21T00:00:00+00:00",
                "summary": {
                    "status": "pass",
                    "scenario_count": 6,
                    "passed_scenarios": 6,
                    "failed_scenarios": 0,
                    "attack_scenarios": 4,
                    "attack_contained": 4,
                    "containment_score_pct": 100.0,
                    "min_containment_score_pct": 100.0,
                    "max_failed_scenarios": 0,
                },
            },
        )

        return {
            "perf": perf,
            "fault": fault,
            "mission": mission,
            "runtime": runtime,
            "journey": journey,
            "distribution": distribution,
            "macos": macos,
            "injection": injection,
        }

    @staticmethod
    def _write_baseline(path: Path) -> None:
        payload = {
            "suite": "release_quality_dashboard_baseline_v1",
            "signals": [
                {"metric_id": "perf.p95_latency_ms", "value": 350.0},
                {"metric_id": "perf.error_rate_pct", "value": 0.0},
                {"metric_id": "fault_injection.pass_rate_pct", "value": 100.0},
                {"metric_id": "mission_queue.success_rate_pct", "value": 99.0},
                {"metric_id": "mission_queue.p95_queue_wait_ms", "value": 1500.0},
                {"metric_id": "mission_queue.p95_end_to_end_ms", "value": 5000.0},
                {"metric_id": "mission_queue.failed_or_canceled", "value": 0.0},
                {"metric_id": "runtime_lifecycle.targets_ok", "value": 1.0},
                {"metric_id": "runtime_lifecycle.startup_ok", "value": 1.0},
                {"metric_id": "runtime_lifecycle.checks_failed", "value": 0.0},
                {"metric_id": "user_journey.success_rate_pct", "value": 100.0},
                {"metric_id": "user_journey.p95_journey_latency_ms", "value": 3000.0},
                {"metric_id": "user_journey.p95_plan_dispatch_latency_ms", "value": 1200.0},
                {"metric_id": "user_journey.p95_execute_dispatch_latency_ms", "value": 1200.0},
                {"metric_id": "user_journey.plan_to_execute_conversion_rate_pct", "value": 100.0},
                {"metric_id": "distribution_resilience.status", "value": 1.0},
                {"metric_id": "distribution_resilience.checks_failed", "value": 0.0},
                {"metric_id": "distribution_resilience.score_pct", "value": 100.0},
                {"metric_id": "macos_desktop_parity.status", "value": 1.0},
                {"metric_id": "macos_desktop_parity.checks_failed", "value": 0.0},
                {"metric_id": "macos_desktop_parity.error_rate_pct", "value": 0.0},
                {"metric_id": "injection_containment.containment_score_pct", "value": 100.0},
                {"metric_id": "injection_containment.failed_scenarios", "value": 0.0},
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_snapshot_and_trend_are_generated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base)
            baseline = base / "baseline.json"
            snapshot = base / "dashboard.json"
            trend = base / "trend.json"
            self._write_baseline(baseline)

            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(reports["mission"]),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--baseline",
                str(baseline),
                "--output",
                str(snapshot),
                "--trend-output",
                str(trend),
                "--release-id",
                "v-test",
                "--release-channel",
                "stable",
                "--commit-sha",
                "deadbeef",
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[quality-dashboard] OK", proc.stdout)
            self.assertTrue(snapshot.exists())
            self.assertTrue(trend.exists())

            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("suite"), "release_quality_dashboard_v1")
            self.assertEqual(payload.get("summary", {}).get("status"), "pass")
            self.assertEqual(int(payload.get("summary", {}).get("signals_total", 0)), 15)

            trend_payload = json.loads(trend.read_text(encoding="utf-8"))
            self.assertEqual(trend_payload.get("suite"), "release_quality_dashboard_trend_v1")
            self.assertEqual(int(trend_payload.get("summary", {}).get("compared_metrics", 0)), 15)

    def test_snapshot_and_trend_include_distribution_when_report_provided(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base)
            baseline = base / "baseline.json"
            snapshot = base / "dashboard.json"
            trend = base / "trend.json"
            self._write_baseline(baseline)

            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(reports["mission"]),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--distribution-resilience-report",
                str(reports["distribution"]),
                "--baseline",
                str(baseline),
                "--output",
                str(snapshot),
                "--trend-output",
                str(trend),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("summary", {}).get("status"), "pass")
            self.assertEqual(int(payload.get("summary", {}).get("signals_total", 0)), 18)
            trend_payload = json.loads(trend.read_text(encoding="utf-8"))
            self.assertEqual(int(trend_payload.get("summary", {}).get("compared_metrics", 0)), 18)

    def test_snapshot_and_trend_include_macos_staging_when_report_provided(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base)
            baseline = base / "baseline.json"
            snapshot = base / "dashboard.json"
            trend = base / "trend.json"
            self._write_baseline(baseline)

            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(reports["mission"]),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--macos-desktop-parity-report",
                str(reports["macos"]),
                "--baseline",
                str(baseline),
                "--output",
                str(snapshot),
                "--trend-output",
                str(trend),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("summary", {}).get("status"), "pass")
            self.assertEqual(int(payload.get("summary", {}).get("signals_total", 0)), 18)
            trend_payload = json.loads(trend.read_text(encoding="utf-8"))
            self.assertEqual(int(trend_payload.get("summary", {}).get("compared_metrics", 0)), 18)
            metric_ids = {
                str(item.get("metric_id"))
                for item in payload.get("signals", [])
                if isinstance(item, dict)
            }
            self.assertIn("macos_desktop_parity.status", metric_ids)
            self.assertIn("macos_desktop_parity.checks_failed", metric_ids)
            self.assertIn("macos_desktop_parity.error_rate_pct", metric_ids)

    def test_snapshot_and_trend_include_injection_containment_when_report_provided(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base)
            baseline = base / "baseline.json"
            snapshot = base / "dashboard.json"
            trend = base / "trend.json"
            self._write_baseline(baseline)

            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(reports["mission"]),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--injection-containment-report",
                str(reports["injection"]),
                "--baseline",
                str(baseline),
                "--output",
                str(snapshot),
                "--trend-output",
                str(trend),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("summary", {}).get("status"), "pass")
            self.assertEqual(int(payload.get("summary", {}).get("signals_total", 0)), 17)
            trend_payload = json.loads(trend.read_text(encoding="utf-8"))
            self.assertEqual(int(trend_payload.get("summary", {}).get("compared_metrics", 0)), 17)
            metric_ids = {
                str(item.get("metric_id"))
                for item in payload.get("signals", [])
                if isinstance(item, dict)
            }
            self.assertIn("injection_containment.containment_score_pct", metric_ids)
            self.assertIn("injection_containment.failed_scenarios", metric_ids)

    def test_snapshot_fails_when_quality_signal_breaches_threshold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base, perf_p95=900.0)
            baseline = base / "baseline.json"
            snapshot = base / "dashboard.json"
            trend = base / "trend.json"
            self._write_baseline(baseline)

            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(reports["mission"]),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--baseline",
                str(baseline),
                "--output",
                str(snapshot),
                "--trend-output",
                str(trend),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[quality-dashboard] FAILED", proc.stdout)
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("summary", {}).get("status"), "fail")

    def test_snapshot_fails_when_distribution_signal_breaches_threshold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base, distribution_status="fail")
            baseline = base / "baseline.json"
            snapshot = base / "dashboard.json"
            trend = base / "trend.json"
            self._write_baseline(baseline)

            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(reports["mission"]),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--distribution-resilience-report",
                str(reports["distribution"]),
                "--baseline",
                str(baseline),
                "--output",
                str(snapshot),
                "--trend-output",
                str(trend),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("summary", {}).get("status"), "fail")

    def test_snapshot_fails_when_macos_staging_signal_breaches_threshold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base, macos_status="fail")
            baseline = base / "baseline.json"
            snapshot = base / "dashboard.json"
            trend = base / "trend.json"
            self._write_baseline(baseline)

            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(reports["mission"]),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--macos-desktop-parity-report",
                str(reports["macos"]),
                "--baseline",
                str(baseline),
                "--output",
                str(snapshot),
                "--trend-output",
                str(trend),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("summary", {}).get("status"), "fail")

    def test_snapshot_fails_when_required_report_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-quality-dashboard-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base=base)
            snapshot = base / "dashboard.json"
            proc = self._run(
                "--perf-report",
                str(reports["perf"]),
                "--fault-injection-report",
                str(reports["fault"]),
                "--mission-queue-report",
                str(base / "missing-mission.json"),
                "--runtime-lifecycle-report",
                str(reports["runtime"]),
                "--user-journey-report",
                str(reports["journey"]),
                "--output",
                str(snapshot),
                "--trend-output",
                "",
            )
            self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("missing report", proc.stderr.lower())


if __name__ == "__main__":
    unittest.main()
