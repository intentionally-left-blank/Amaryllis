from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class FaultInjectionReliabilityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "fault_injection_reliability_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_fault_injection_gate_passes_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-fault-injection-test-") as tmp:
            report_path = Path(tmp) / "fault-injection-report.json"
            proc = self._run(
                "--retry-max-attempts",
                "2",
                "--scenario-timeout-sec",
                "6",
                "--min-pass-rate-pct",
                "100",
                "--output",
                str(report_path),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[fault-injection] OK", proc.stdout)
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report.get("suite"), "fault_injection_reliability_v1")
            summary = dict(report.get("summary") or {})
            self.assertEqual(int(summary.get("scenario_count", 0)), 3)
            self.assertEqual(int(summary.get("failed", 0)), 0)

    def test_fault_injection_gate_fails_when_retries_are_disabled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-fault-injection-test-") as tmp:
            report_path = Path(tmp) / "fault-injection-report.json"
            proc = self._run(
                "--retry-max-attempts",
                "1",
                "--scenario-timeout-sec",
                "6",
                "--min-pass-rate-pct",
                "100",
                "--output",
                str(report_path),
            )
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[fault-injection] FAILED", proc.stdout)

    def test_fault_injection_gate_validates_min_pass_rate_range(self) -> None:
        proc = self._run("--min-pass-rate-pct", "101")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("--min-pass-rate-pct must be in range 0..100", proc.stderr)


if __name__ == "__main__":
    unittest.main()
