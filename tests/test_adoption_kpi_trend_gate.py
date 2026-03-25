from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AdoptionKPITrendGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "adoption_kpi_trend_gate.py"

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _write_reports(self, base: Path, *, install_success_pct: float = 100.0) -> dict[str, Path]:
        snapshot = base / "adoption-kpi-snapshot.json"
        baseline = base / "adoption-kpi-baseline.json"
        signals_snapshot = [
            {"metric_id": "user_journey.activation_success_rate_pct", "value": 100.0},
            {"metric_id": "user_journey.activation_blocked_rate_pct", "value": 0.0},
            {"metric_id": "user_journey.install_success_rate_pct", "value": install_success_pct},
            {"metric_id": "user_journey.retention_proxy_success_rate_pct", "value": 100.0},
            {"metric_id": "user_journey.feature_adoption_rate_pct", "value": 100.0},
            {"metric_id": "api_quickstart_compat.pass_rate_pct", "value": 100.0},
            {"metric_id": "distribution_channel_manifest.coverage_pct", "value": 100.0},
        ]
        signals_baseline = [
            {"metric_id": "user_journey.activation_success_rate_pct", "value": 100.0},
            {"metric_id": "user_journey.activation_blocked_rate_pct", "value": 0.0},
            {"metric_id": "user_journey.install_success_rate_pct", "value": 100.0},
            {"metric_id": "user_journey.retention_proxy_success_rate_pct", "value": 100.0},
            {"metric_id": "user_journey.feature_adoption_rate_pct", "value": 100.0},
            {"metric_id": "api_quickstart_compat.pass_rate_pct", "value": 100.0},
            {"metric_id": "distribution_channel_manifest.coverage_pct", "value": 100.0},
        ]
        self._write_json(
            snapshot,
            {
                "suite": "adoption_kpi_snapshot_v1",
                "signals": signals_snapshot,
                "summary": {"status": "pass"},
            },
        )
        self._write_json(
            baseline,
            {
                "suite": "adoption_kpi_snapshot_baseline_v1",
                "signals": signals_baseline,
            },
        )
        return {"snapshot": snapshot, "baseline": baseline}

    def test_help_contract(self) -> None:
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        text = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("--snapshot-report", text)
        self.assertIn("--baseline", text)
        self.assertIn("--max-install-success-regression-pct", text)
        self.assertIn("--output", text)

    def test_gate_passes_with_no_regression(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-adoption-trend-gate-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base, install_success_pct=100.0)
            proc = self._run(
                "--snapshot-report",
                str(reports["snapshot"]),
                "--baseline",
                str(reports["baseline"]),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[adoption-kpi-trend-gate] OK", proc.stdout)

    def test_gate_fails_when_install_regression_exceeds_threshold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-adoption-trend-gate-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base, install_success_pct=90.0)
            proc = self._run(
                "--snapshot-report",
                str(reports["snapshot"]),
                "--baseline",
                str(reports["baseline"]),
                "--max-install-success-regression-pct",
                "0",
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[adoption-kpi-trend-gate] FAILED", proc.stdout)

    def test_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-adoption-trend-gate-") as tmp:
            base = Path(tmp)
            reports = self._write_reports(base, install_success_pct=100.0)
            output = base / "adoption-kpi-trend-gate-report.json"
            proc = self._run(
                "--snapshot-report",
                str(reports["snapshot"]),
                "--baseline",
                str(reports["baseline"]),
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "adoption_kpi_trend_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")


if __name__ == "__main__":
    unittest.main()
