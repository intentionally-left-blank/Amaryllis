from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime.observability import ObservabilityManager, SLOTargets


class ObservabilityTests(unittest.TestCase):
    def _build_manager(self) -> ObservabilityManager:
        return ObservabilityManager(
            logger=logging.getLogger("amaryllis.tests.observability"),
            service_name="amaryllis-test",
            service_version="0.0-test",
            environment="test",
            otel_enabled=False,
            otlp_endpoint=None,
            slo_targets=SLOTargets(
                window_sec=60.0,
                request_availability_target=0.95,
                request_latency_p95_ms_target=100.0,
                run_success_target=0.9,
                min_request_samples=5,
                min_run_samples=3,
                incident_cooldown_sec=1.0,
            ),
        )

    def test_sre_snapshot_and_prometheus_metrics(self) -> None:
        manager = self._build_manager()
        for _ in range(5):
            manager.sre.record_http(
                method="GET",
                path="/models",
                status_code=200,
                duration_ms=40.0,
            )
        for _ in range(3):
            manager.sre.record_run_terminal(status="succeeded")

        snapshot = manager.sre.snapshot()
        self.assertIn("sli", snapshot)
        self.assertGreaterEqual(float(snapshot["sli"]["requests"]["availability"]), 0.99)
        self.assertGreaterEqual(float(snapshot["sli"]["runs"]["success_rate"]), 0.99)

        metrics = manager.sre.render_prometheus_metrics()
        self.assertIn("amaryllis_request_availability_ratio", metrics)
        self.assertIn("amaryllis_run_success_ratio", metrics)
        self.assertIn("amaryllis_release_quality_snapshot_loaded 0", metrics)
        self.assertIn("amaryllis_adoption_snapshot_loaded 0", metrics)
        self.assertIn("amaryllis_nightly_mission_snapshot_loaded 0", metrics)

    def test_release_quality_snapshot_metrics_are_exported_when_configured(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-observability-") as tmp:
            snapshot_path = Path(tmp) / "release-quality-dashboard.json"
            payload = {
                "suite": "release_quality_dashboard_v1",
                "summary": {
                    "signals_total": 21,
                    "signals_passed": 21,
                    "signals_failed": 0,
                    "quality_score_pct": 100.0,
                    "status": "pass",
                },
                "signals": [
                    {"metric_id": "macos_desktop_parity.status", "value": 1.0},
                    {"metric_id": "macos_desktop_parity.error_rate_pct", "value": 0.0},
                    {"metric_id": "macos_desktop_parity.checks_failed", "value": 0.0},
                    {"metric_id": "qos_governor.status", "value": 1.0},
                    {"metric_id": "qos_governor.checks_failed", "value": 0.0},
                    {"metric_id": "user_journey.install_success_rate_pct", "value": 100.0},
                    {"metric_id": "user_journey.retention_proxy_success_rate_pct", "value": 100.0},
                    {"metric_id": "user_journey.feature_adoption_rate_pct", "value": 100.0},
                    {"metric_id": "distribution_channel_manifest.coverage_pct", "value": 100.0},
                    {"metric_id": "api_quickstart_compat.pass_rate_pct", "value": 100.0},
                ],
            }
            snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH": str(snapshot_path)},
                clear=False,
            ):
                manager = self._build_manager()

            metrics = manager.sre.render_prometheus_metrics()
            self.assertIn("amaryllis_release_quality_snapshot_loaded 1", metrics)
            self.assertIn("amaryllis_release_quality_score_pct 100.000000", metrics)
            self.assertIn("amaryllis_release_quality_status 1.000000", metrics)
            self.assertIn("amaryllis_release_desktop_staging_signal_present 1", metrics)
            self.assertIn("amaryllis_release_desktop_staging_status 1.000000", metrics)
            self.assertIn("amaryllis_release_desktop_staging_error_rate_pct 0.000000", metrics)
            self.assertIn("amaryllis_release_qos_signal_present 1", metrics)
            self.assertIn("amaryllis_release_qos_status 1.000000", metrics)
            self.assertIn("amaryllis_release_qos_checks_failed 0.000000", metrics)
            self.assertIn("amaryllis_release_adoption_install_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_release_adoption_retention_proxy_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_release_adoption_feature_adoption_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_release_adoption_channel_manifest_coverage_pct 100.000000", metrics)
            self.assertIn("amaryllis_release_adoption_api_quickstart_pass_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_snapshot_loaded 1", metrics)
            self.assertIn("amaryllis_adoption_status 1.000000", metrics)
            self.assertIn("amaryllis_adoption_score_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_install_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_retention_proxy_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_feature_adoption_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_channel_manifest_coverage_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_api_quickstart_pass_rate_pct 100.000000", metrics)

    def test_adoption_snapshot_metrics_are_exported_when_configured(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-observability-") as tmp:
            snapshot_path = Path(tmp) / "adoption-kpi-snapshot.json"
            payload = {
                "suite": "adoption_kpi_snapshot_v1",
                "summary": {
                    "status": "pass",
                    "adoption_score_pct": 100.0,
                },
                "kpis": {
                    "adoption_schema_checks_failed": 0,
                    "journey_activation_success_rate_pct": 100.0,
                    "journey_activation_blocked_rate_pct": 0.0,
                    "journey_install_success_rate_pct": 100.0,
                    "journey_retention_proxy_success_rate_pct": 100.0,
                    "journey_feature_adoption_rate_pct": 100.0,
                    "distribution_channel_manifest_coverage_pct": 100.0,
                    "api_quickstart_pass_rate_pct": 100.0,
                },
            }
            snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"AMARYLLIS_ADOPTION_KPI_SNAPSHOT_PATH": str(snapshot_path)},
                clear=False,
            ):
                manager = self._build_manager()

            metrics = manager.sre.render_prometheus_metrics()
            self.assertIn("amaryllis_adoption_snapshot_loaded 1", metrics)
            self.assertIn("amaryllis_adoption_status 1.000000", metrics)
            self.assertIn("amaryllis_adoption_score_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_schema_checks_failed 0.000000", metrics)
            self.assertIn("amaryllis_adoption_activation_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_activation_blocked_rate_pct 0.000000", metrics)
            self.assertIn("amaryllis_adoption_install_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_retention_proxy_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_feature_adoption_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_channel_manifest_coverage_pct 100.000000", metrics)
            self.assertIn("amaryllis_adoption_api_quickstart_pass_rate_pct 100.000000", metrics)

    def test_nightly_mission_snapshot_metrics_are_exported_when_configured(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-observability-") as tmp:
            snapshot_path = Path(tmp) / "nightly-mission-success-recovery.json"
            payload = {
                "suite": "mission_success_recovery_report_pack_v2",
                "scope": "nightly",
                "summary": {
                    "checks_total": 4,
                    "checks_passed": 4,
                    "checks_failed": 0,
                    "status": "pass",
                },
                "kpis": {
                    "nightly_success_rate_pct": 100.0,
                    "nightly_p95_latency_ms": 280.5,
                    "nightly_latency_jitter_ms": 42.0,
                    "nightly_burn_rate_gate_passed": True,
                    "nightly_adoption_trend_gate_passed": True,
                    "nightly_adoption_trend_regressed_metrics": 0,
                    "nightly_breaker_soak_gate_passed": True,
                    "nightly_breaker_soak_cycles_failed": 0,
                    "nightly_breaker_soak_p95_cycle_latency_ms": 512.0,
                },
            }
            snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH": str(snapshot_path)},
                clear=False,
            ):
                manager = self._build_manager()

            metrics = manager.sre.render_prometheus_metrics()
            self.assertIn("amaryllis_nightly_mission_snapshot_loaded 1", metrics)
            self.assertIn("amaryllis_nightly_mission_status 1.000000", metrics)
            self.assertIn("amaryllis_nightly_success_rate_pct 100.000000", metrics)
            self.assertIn("amaryllis_nightly_p95_latency_ms 280.500000", metrics)
            self.assertIn("amaryllis_nightly_latency_jitter_ms 42.000000", metrics)
            self.assertIn("amaryllis_nightly_burn_rate_gate_passed 1.000000", metrics)
            self.assertIn("amaryllis_nightly_adoption_trend_gate_passed 1.000000", metrics)
            self.assertIn("amaryllis_nightly_adoption_trend_regressed_metrics 0.000000", metrics)
            self.assertIn("amaryllis_nightly_breaker_soak_gate_passed 1.000000", metrics)
            self.assertIn("amaryllis_nightly_breaker_soak_cycles_failed 0.000000", metrics)
            self.assertIn("amaryllis_nightly_breaker_soak_p95_cycle_latency_ms 512.000000", metrics)

    def test_incident_is_detected_when_slo_is_breached(self) -> None:
        manager = self._build_manager()
        # Breach request availability and latency SLO.
        for _ in range(3):
            manager.sre.record_http(
                method="GET",
                path="/models",
                status_code=500,
                duration_ms=250.0,
            )
        for _ in range(3):
            manager.sre.record_http(
                method="GET",
                path="/models",
                status_code=200,
                duration_ms=250.0,
            )
        # Breach run success SLO.
        manager.sre.record_run_terminal(status="failed")
        manager.sre.record_run_terminal(status="failed")
        manager.sre.record_run_terminal(status="succeeded")

        incidents = manager.sre.list_incidents(limit=50)
        self.assertGreaterEqual(len(incidents), 1)
        incident_types = {str(item.get("type")) for item in incidents}
        self.assertTrue(
            bool({"request_availability", "request_latency_p95", "run_success_rate"} & incident_types)
        )

    def test_generation_loop_metrics_are_ingested_and_exported(self) -> None:
        manager = self._build_manager()
        manager.sre.ingest_event(
            "generation_loop_metrics",
            {
                "request_id": "req-1",
                "provider": "mlx",
                "model": "mlx-community/test",
                "stream": True,
                "fallback_used": True,
                "ttft_ms": 120.5,
                "total_latency_ms": 530.0,
                "thermal_state": "hot",
                "kv_cache": {"pressure_state": "high"},
            },
        )

        snapshot = manager.sre.snapshot()
        generation = snapshot["sli"]["generation"]
        self.assertEqual(int(generation["total"]), 1)
        self.assertEqual(int(generation["stream_total"]), 1)
        self.assertEqual(int(generation["fallback_total"]), 1)
        self.assertGreater(float(generation["ttft_p95_ms"]), 0.0)
        self.assertEqual(int(generation["kv_pressure_events"]), 1)
        self.assertEqual(int(generation["thermal_hot_events"]), 1)

        metrics = manager.sre.render_prometheus_metrics()
        self.assertIn("amaryllis_generation_events_total 1", metrics)
        self.assertIn("amaryllis_generation_kv_pressure_events_total 1", metrics)
        self.assertIn("amaryllis_generation_thermal_hot_events_total 1", metrics)


if __name__ == "__main__":
    unittest.main()
