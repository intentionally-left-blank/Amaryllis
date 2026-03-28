from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class FlowInteractionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "flow_interaction_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_gate_passes_with_default_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-flow-interaction-gate-test-") as tmp:
            output = Path(tmp) / "flow-interaction-gate-report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[flow-interaction-gate] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "flow_interaction_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")

    def test_gate_fails_when_interaction_doc_missing(self) -> None:
        proc = self._run("--interaction-doc", "docs/missing-agent-run-interaction-modes.md")
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[flow-interaction-gate] FAILED", proc.stdout)
        self.assertIn("interaction_doc_exists", proc.stdout)


if __name__ == "__main__":
    unittest.main()
