from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


class RuntimeProfileDriftCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "check_runtime_profile_drift.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_profile_drift_check_passes_for_default_profiles(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[profile-drift] OK", proc.stdout)

    def test_profile_drift_check_fails_for_missing_manifest_directory(self) -> None:
        proc = self._run("--runtime-profiles-dir", "runtime/missing-profiles")
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[profile-drift] FAILED", proc.stderr)


if __name__ == "__main__":
    unittest.main()
