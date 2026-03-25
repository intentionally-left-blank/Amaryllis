from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AdoptionKPISchemaGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "adoption_kpi_schema_gate.py"

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

    def _write_sources(self, base: Path, *, drop_journey_feature_metric: bool = False) -> dict[str, Path]:
        journey = base / "user-journey.json"
        api_quickstart = base / "api-quickstart.json"
        distribution_manifest = base / "distribution-channel-manifest.json"
        quality_dashboard = base / "release-quality-dashboard.json"

        journey_summary = {
            "activation_success_rate_pct": 100.0,
            "activation_blocked_rate_pct": 0.0,
            "install_success_rate_pct": 100.0,
            "retention_proxy_success_rate_pct": 100.0,
            "feature_adoption_rate_pct": 100.0,
        }
        if drop_journey_feature_metric:
            journey_summary.pop("feature_adoption_rate_pct", None)

        self._write_json(
            journey,
            {
                "suite": "user_journey_benchmark_v1",
                "config": {
                    "thresholds": {
                        "min_activation_success_rate_pct": 100.0,
                        "max_blocked_activation_rate_pct": 0.0,
                        "min_install_success_rate_pct": 100.0,
                        "min_retention_proxy_success_rate_pct": 100.0,
                        "min_feature_adoption_rate_pct": 100.0,
                    }
                },
                "summary": journey_summary,
            },
        )
        self._write_json(
            api_quickstart,
            {
                "suite": "api_quickstart_compatibility_gate_v1",
                "summary": {
                    "status": "pass",
                    "checks_total": 16,
                    "checks_failed": 0,
                },
            },
        )
        self._write_json(
            distribution_manifest,
            {
                "suite": "distribution_channel_manifest_gate_v1",
                "summary": {
                    "status": "pass",
                    "checks_total": 4,
                    "checks_failed": 0,
                },
            },
        )
        self._write_json(
            quality_dashboard,
            {
                "suite": "release_quality_dashboard_v1",
                "signals": [
                    {"metric_id": "user_journey.activation_success_rate_pct", "value": 100.0, "passed": True},
                    {"metric_id": "user_journey.install_success_rate_pct", "value": 100.0, "passed": True},
                    {"metric_id": "user_journey.retention_proxy_success_rate_pct", "value": 100.0, "passed": True},
                    {"metric_id": "user_journey.feature_adoption_rate_pct", "value": 100.0, "passed": True},
                    {"metric_id": "api_quickstart_compat.pass_rate_pct", "value": 100.0, "passed": True},
                    {"metric_id": "distribution_channel_manifest.coverage_pct", "value": 100.0, "passed": True},
                ],
            },
        )
        return {
            "journey": journey,
            "api_quickstart": api_quickstart,
            "distribution_manifest": distribution_manifest,
            "quality_dashboard": quality_dashboard,
        }

    def test_gate_passes_with_valid_sources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-adoption-kpi-schema-gate-test-") as tmp:
            base = Path(tmp)
            reports = self._write_sources(base)
            proc = self._run(
                "--user-journey-report",
                str(reports["journey"]),
                "--api-quickstart-report",
                str(reports["api_quickstart"]),
                "--distribution-channel-manifest-report",
                str(reports["distribution_manifest"]),
                "--quality-dashboard-report",
                str(reports["quality_dashboard"]),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[adoption-kpi-schema-gate] OK", proc.stdout)

    def test_gate_fails_when_required_journey_metric_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-adoption-kpi-schema-gate-test-") as tmp:
            base = Path(tmp)
            reports = self._write_sources(base, drop_journey_feature_metric=True)
            proc = self._run(
                "--user-journey-report",
                str(reports["journey"]),
                "--api-quickstart-report",
                str(reports["api_quickstart"]),
                "--distribution-channel-manifest-report",
                str(reports["distribution_manifest"]),
                "--quality-dashboard-report",
                str(reports["quality_dashboard"]),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[adoption-kpi-schema-gate] FAILED", proc.stdout)
            self.assertIn("journey.required_metrics_present", proc.stdout)

    def test_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-adoption-kpi-schema-gate-test-") as tmp:
            base = Path(tmp)
            reports = self._write_sources(base)
            output = base / "adoption-kpi-schema-gate-report.json"
            proc = self._run(
                "--user-journey-report",
                str(reports["journey"]),
                "--api-quickstart-report",
                str(reports["api_quickstart"]),
                "--distribution-channel-manifest-report",
                str(reports["distribution_manifest"]),
                "--quality-dashboard-report",
                str(reports["quality_dashboard"]),
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "adoption_kpi_schema_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")
            self.assertIn("journey_install_success_rate_pct", payload.get("kpis", {}))

    def test_gate_validates_min_api_quickstart_range(self) -> None:
        proc = self._run("--min-api-quickstart-pass-rate-pct", "101")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("--min-api-quickstart-pass-rate-pct must be in range 0..100", proc.stderr)


if __name__ == "__main__":
    unittest.main()
