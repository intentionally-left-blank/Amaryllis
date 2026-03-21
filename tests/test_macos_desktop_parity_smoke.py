from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MacOSDesktopParitySmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "macos_desktop_parity_smoke.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_smoke_passes_single_iteration_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-macos-desktop-parity-") as tmp:
            output = Path(tmp) / "macos-desktop-parity-report.json"
            proc = self._run("--iterations", "1", "--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[macos-desktop-parity] OK", proc.stdout)
            self.assertTrue(output.exists(), "report file must be written")
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "macos_desktop_parity_smoke_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")
            self.assertGreaterEqual(int(payload.get("summary", {}).get("checks_total", 0)), 1)

    def test_smoke_rejects_zero_iterations(self) -> None:
        proc = self._run("--iterations", "0")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("--iterations must be >= 1", proc.stderr)


if __name__ == "__main__":
    unittest.main()
