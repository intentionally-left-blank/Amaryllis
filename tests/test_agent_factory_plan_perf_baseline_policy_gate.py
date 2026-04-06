from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentFactoryPlanPerfBaselinePolicyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "agent_factory_plan_perf_baseline_policy_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def _write_baseline(
        self,
        path: Path,
        *,
        release_p95: float,
        nightly_p95: float,
        release_error: float = 0.0,
        nightly_error: float = 0.0,
        change_control: dict[str, object] | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "suite": "agent_factory_plan_perf_envelope_v1",
            "profiles": {
                "release": {
                    "requests_total": 30,
                    "concurrency": 6,
                    "max_p95_latency_ms": release_p95,
                    "max_error_rate_pct": release_error,
                },
                "nightly": {
                    "requests_total": 40,
                    "concurrency": 8,
                    "max_p95_latency_ms": nightly_p95,
                    "max_error_rate_pct": nightly_error,
                },
            },
        }
        if change_control is not None:
            payload["change_control"] = change_control
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_gate_passes_within_auto_drift_limits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-baseline-policy-") as tmp:
            tmp_dir = Path(tmp)
            reference = tmp_dir / "reference.json"
            current = tmp_dir / "current.json"
            report = tmp_dir / "report.json"

            self._write_baseline(reference, release_p95=1000.0, nightly_p95=1500.0)
            self._write_baseline(
                current,
                release_p95=1080.0,
                nightly_p95=1440.0,
                change_control={
                    "change_id": "af-refresh-001",
                    "reason": "Minor recalibration from weekly sample.",
                    "ticket": "P8-A13",
                    "requested_by": "release-bot",
                    "manual_approval": False,
                },
            )

            proc = self._run(
                "--reference-baseline",
                str(reference),
                "--current-baseline",
                str(current),
                "--output",
                str(report),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[agent-factory-plan-perf-baseline-policy-gate] OK", proc.stdout)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite") or ""), "agent_factory_plan_perf_baseline_policy_gate_v1")
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status") or ""), "pass")
            self.assertEqual(int(summary.get("profiles_requiring_manual_approval", -1)), 0)

    def test_gate_allows_large_drift_with_manual_approval_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-baseline-policy-") as tmp:
            tmp_dir = Path(tmp)
            reference = tmp_dir / "reference.json"
            current = tmp_dir / "current.json"
            report = tmp_dir / "report.json"

            self._write_baseline(reference, release_p95=1000.0, nightly_p95=1500.0)
            self._write_baseline(
                current,
                release_p95=1400.0,
                nightly_p95=1500.0,
                change_control={
                    "change_id": "af-refresh-002",
                    "reason": "Measured hardware drift in CI runner class.",
                    "ticket": "P8-A13",
                    "requested_by": "release-bot",
                    "manual_approval": True,
                    "approved_by": ["maintainer-1"],
                    "approved_at": "2026-04-07T00:00:00+00:00",
                    "approval_scope": ["release"],
                },
            )

            proc = self._run(
                "--reference-baseline",
                str(reference),
                "--current-baseline",
                str(current),
                "--output",
                str(report),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(report.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status") or ""), "pass")
            self.assertEqual(int(summary.get("profiles_requiring_manual_approval") or 0), 1)

    def test_gate_fails_for_large_drift_without_manual_approval_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-baseline-policy-") as tmp:
            tmp_dir = Path(tmp)
            reference = tmp_dir / "reference.json"
            current = tmp_dir / "current.json"
            report = tmp_dir / "report.json"

            self._write_baseline(reference, release_p95=1000.0, nightly_p95=1500.0)
            self._write_baseline(
                current,
                release_p95=1400.0,
                nightly_p95=1500.0,
                change_control={
                    "change_id": "af-refresh-003",
                    "reason": "Large shift but metadata incomplete.",
                    "ticket": "P8-A13",
                    "requested_by": "release-bot",
                    "manual_approval": False,
                },
            )

            proc = self._run(
                "--reference-baseline",
                str(reference),
                "--current-baseline",
                str(current),
                "--output",
                str(report),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[agent-factory-plan-perf-baseline-policy-gate] FAILED", proc.stdout)
            payload = json.loads(report.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status") or ""), "fail")
            self.assertGreater(int(summary.get("checks_failed") or 0), 0)

    def test_gate_fails_when_change_control_missing_for_changed_baseline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-baseline-policy-") as tmp:
            tmp_dir = Path(tmp)
            reference = tmp_dir / "reference.json"
            current = tmp_dir / "current.json"
            report = tmp_dir / "report.json"

            self._write_baseline(reference, release_p95=1000.0, nightly_p95=1500.0)
            self._write_baseline(current, release_p95=1100.0, nightly_p95=1500.0, change_control=None)

            proc = self._run(
                "--reference-baseline",
                str(reference),
                "--current-baseline",
                str(current),
                "--output",
                str(report),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(report.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status") or ""), "fail")
            metadata_checks = payload.get("metadata_checks", [])
            self.assertTrue(any(str(check.get("name")) == "change_control" for check in metadata_checks if isinstance(check, dict)))


if __name__ == "__main__":
    unittest.main()
