from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AutonomyCircuitBreakerSoakGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "autonomy_circuit_breaker_soak_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_soak_gate_passes_with_three_scope_cycles(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-autonomy-circuit-breaker-soak-gate-test-") as tmp:
            output = Path(tmp) / "autonomy-circuit-breaker-soak-gate-report.json"
            proc = self._run(
                "--cycles",
                "3",
                "--max-p95-cycle-latency-ms",
                "60000",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[autonomy-circuit-breaker-soak-gate] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "autonomy_circuit_breaker_soak_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")
            self.assertEqual(int(payload.get("summary", {}).get("cycles_total", 0)), 3)
            scopes = set(payload.get("summary", {}).get("scopes_covered", []))
            self.assertTrue({"global", "user", "agent"}.issubset(scopes))

    def test_soak_gate_rejects_invalid_cycle_count(self) -> None:
        proc = self._run("--cycles", "0")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("--cycles must be >= 1", proc.stderr)


if __name__ == "__main__":
    unittest.main()
