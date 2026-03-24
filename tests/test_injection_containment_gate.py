from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InjectionContainmentGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "injection_containment_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_injection_containment_gate_passes_default(self) -> None:
        proc = self._run("--min-containment-score-pct", "100", "--max-failed-scenarios", "0")
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[injection-containment] OK", proc.stdout)

    def test_injection_containment_gate_fails_when_required_scenario_missing(self) -> None:
        proc = self._run("--require-scenario", "missing-scenario")
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[injection-containment] FAILED", proc.stdout)
        self.assertIn("missing_required_scenarios:missing-scenario", proc.stdout)

    def test_injection_containment_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-injection-gate-test-") as tmp:
            output = Path(tmp) / "report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report.get("suite"), "injection_containment_gate_v1")
            self.assertEqual(str(report.get("summary", {}).get("status")), "pass")

    def test_injection_containment_gate_validates_min_containment_range(self) -> None:
        proc = self._run("--min-containment-score-pct", "101")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("--min-containment-score-pct must be in range 0..100", proc.stderr)


if __name__ == "__main__":
    unittest.main()
