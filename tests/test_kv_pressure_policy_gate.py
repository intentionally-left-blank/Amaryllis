from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class KVPressurePolicyGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "kv_pressure_policy_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_kv_pressure_policy_gate_passes_default(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[kv-pressure-policy-gate] OK", proc.stdout)

    def test_kv_pressure_policy_gate_fails_with_impossible_critical_threshold(self) -> None:
        proc = self._run("--min-critical-events", "99")
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[kv-pressure-policy-gate] FAILED", proc.stdout)
        self.assertIn("telemetry:critical_events_below_min", proc.stdout)

    def test_kv_pressure_policy_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-kv-pressure-policy-gate-test-") as tmp:
            output = Path(tmp) / "report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "kv_pressure_policy_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")


if __name__ == "__main__":
    unittest.main()
