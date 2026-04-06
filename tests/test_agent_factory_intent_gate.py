from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentFactoryIntentGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "agent_factory_intent_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_gate_passes_default_fixture(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[agent-factory-intent-gate] OK", proc.stdout)

    def test_gate_fails_on_mismatched_fixture_expectations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-gate-") as tmp:
            fixture = Path(tmp) / "fixture.json"
            fixture.write_text(
                json.dumps(
                    {
                        "suite": "agent_factory_intent_inference_v1",
                        "cases": [
                            {
                                "id": "forced_mismatch",
                                "request": "создай агента для AI новостей из reddit",
                                "expected": {
                                    "kind": "coding",
                                    "mixed_intent": False,
                                    "source_policy_mode": "channels",
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            proc = self._run("--fixture", str(fixture))
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[agent-factory-intent-gate] FAILED", proc.stdout)
            self.assertIn("forced_mismatch", proc.stdout)

    def test_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-gate-report-") as tmp:
            output = Path(tmp) / "report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite") or ""), "agent_factory_intent_gate_v1")
            summary = payload.get("summary", {})
            self.assertIsInstance(summary, dict)
            self.assertEqual(str(summary.get("status") or ""), "pass")


if __name__ == "__main__":
    unittest.main()
