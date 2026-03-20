from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AutonomyPolicyPackCheckScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "check_autonomy_policy_pack.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_script_passes_with_default_policy_pack(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[autonomy-policy-pack] OK", proc.stdout)

    def test_script_fails_for_missing_policy_pack(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-autonomy-pack-check-") as tmp:
            missing = Path(tmp) / "missing.json"
            proc = self._run("--policy-pack", str(missing))
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[autonomy-policy-pack] FAILED", proc.stderr)


if __name__ == "__main__":
    unittest.main()
