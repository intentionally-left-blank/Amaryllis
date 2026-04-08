from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentTemplateContractGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "agent_template_contract_gate.py"
        self.snapshot = self.repo_root / "eval" / "fixtures" / "agent_templates" / "template_contract_snapshot.json"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_gate_passes_with_default_fixture(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[agent-template-contract-gate] OK", proc.stdout)

    def test_gate_fails_when_snapshot_drift_detected(self) -> None:
        payload = json.loads(self.snapshot.read_text(encoding="utf-8"))
        cases = payload.get("cases", {})
        self.assertIsInstance(cases, dict)
        if isinstance(cases, dict) and "ai_news_daily_default" in cases:
            target = cases.get("ai_news_daily_default", {})
            if isinstance(target, dict):
                template = target.get("template")
                if isinstance(template, dict):
                    template["name"] = "Drifted Snapshot Name"
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-template-gate-") as tmp:
            drift_snapshot = Path(tmp) / "snapshot.json"
            drift_snapshot.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            proc = self._run("--snapshot", str(drift_snapshot))
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[agent-template-contract-gate] FAILED", proc.stdout)
        self.assertIn("snapshot_drift=ai_news_daily_default", proc.stdout)

    def test_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-template-gate-report-") as tmp:
            output = Path(tmp) / "report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(report.get("suite")), "agent_template_contract_gate_v1")
            summary = report.get("summary", {})
            self.assertIsInstance(summary, dict)
            self.assertEqual(str(summary.get("status")), "pass")


if __name__ == "__main__":
    unittest.main()
