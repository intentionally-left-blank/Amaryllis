from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class QoSGovernorGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "qos_governor_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_qos_governor_gate_passes_default(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[qos-governor-gate] OK", proc.stdout)

    def test_qos_governor_gate_fails_when_expected_final_mode_is_wrong(self) -> None:
        proc = self._run("--expect-final-mode", "balanced")
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[qos-governor-gate] FAILED", proc.stdout)
        self.assertIn("qos_set_thermal_cool_recovery_2:unexpected_mode", proc.stdout)

    def test_qos_governor_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-qos-governor-gate-test-") as tmp:
            output = Path(tmp) / "report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "qos_governor_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")


if __name__ == "__main__":
    unittest.main()
