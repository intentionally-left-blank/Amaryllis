from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LocalizationGovernanceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "localization_governance_gate.py"
        self.required_files = [
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "GOVERNANCE.md",
            "MAINTAINERS.md",
            "TRADEMARK_POLICY.md",
            "DCO.md",
            ".github/PULL_REQUEST_TEMPLATE.md",
            "docs/localization/ru/quickstart.md",
            "docs/localization/ru/starter-prompts.md",
            "docs/localization/ru/starter-workflows.md",
            "docs/localization/en/quickstart.md",
            "docs/localization/en/starter-prompts.md",
            "docs/localization/en/starter-workflows.md",
        ]

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def _create_minimal_root(self, root: Path) -> None:
        for rel in self.required_files:
            src = self.repo_root / rel
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    def test_gate_passes_with_default_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-localization-governance-gate-test-") as tmp:
            output = Path(tmp) / "localization-governance-gate-report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[localization-governance-gate] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "localization_governance_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")

    def test_gate_fails_when_dco_snippet_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-localization-governance-gate-test-") as tmp:
            root = Path(tmp) / "repo"
            self._create_minimal_root(root)
            contributing = root / "CONTRIBUTING.md"
            contributing.write_text(
                contributing.read_text(encoding="utf-8").replace("Signed-off-by:", "SIGN-OFF-REMOVED:"),
                encoding="utf-8",
            )
            proc = self._run("--root", str(root))
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[localization-governance-gate] FAILED", proc.stdout)
            self.assertIn("CONTRIBUTING.md", proc.stdout)


if __name__ == "__main__":
    unittest.main()
