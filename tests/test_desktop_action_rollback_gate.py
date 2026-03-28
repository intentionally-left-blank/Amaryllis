from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DesktopActionRollbackGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "desktop_action_rollback_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_gate_passes_with_default_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-desktop-action-rollback-gate-test-") as tmp:
            output = Path(tmp) / "desktop-action-rollback-gate-report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[desktop-action-rollback-gate] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "desktop_action_rollback_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")

    def test_gate_fails_when_doc_missing(self) -> None:
        proc = self._run("--desktop-doc", "docs/missing-linux-desktop-action-adapters.md")
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[desktop-action-rollback-gate] FAILED", proc.stdout)
        self.assertIn("desktop_doc_exists", proc.stdout)


if __name__ == "__main__":
    unittest.main()
