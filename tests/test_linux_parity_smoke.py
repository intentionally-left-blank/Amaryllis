from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


class LinuxParitySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "linux_parity_smoke.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_linux_parity_smoke_passes_single_iteration(self) -> None:
        proc = self._run("--iterations", "1")
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[linux-parity] OK", proc.stdout)

    def test_linux_parity_smoke_rejects_zero_iterations(self) -> None:
        proc = self._run("--iterations", "0")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[linux-parity] --iterations must be >= 1", proc.stderr)


if __name__ == "__main__":
    unittest.main()
