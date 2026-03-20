from __future__ import annotations

import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


class LinuxInstallerSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "linux_installer_smoke.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_contract(self) -> None:
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        text = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("--require-linux", text)
        self.assertIn("--output", text)
        self.assertIn("--keep-releases", text)

    @unittest.skipUnless(platform.system() != "Linux", "non-Linux behavior check")
    def test_non_linux_skip_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-linux-installer-smoke-test-") as tmp:
            report_path = Path(tmp) / "report.json"
            proc = self._run("--output", str(report_path))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[linux-installer-smoke] SKIPPED", proc.stdout)
            self.assertTrue(report_path.exists(), "report should be created on skip path")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report.get("suite"), "linux_installer_smoke_v1")
            checks = report.get("checks") or []
            platform_check = next((item for item in checks if item.get("name") == "platform_check"), None)
            self.assertIsNotNone(platform_check, "platform_check must be present in report")
            self.assertTrue(bool(platform_check.get("ok")), "platform_check should pass in skip mode")

    @unittest.skipUnless(platform.system() != "Linux", "non-Linux behavior check")
    def test_non_linux_require_linux_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-linux-installer-smoke-test-") as tmp:
            report_path = Path(tmp) / "report.json"
            proc = self._run("--require-linux", "--output", str(report_path))
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[linux-installer-smoke] FAILED", proc.stdout)
            self.assertTrue(report_path.exists(), "report should be created on failure path")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            checks = report.get("checks") or []
            platform_check = next((item for item in checks if item.get("name") == "platform_check"), None)
            self.assertIsNotNone(platform_check, "platform_check must be present in report")
            self.assertFalse(bool(platform_check.get("ok")), "platform_check should fail in require-linux mode")


if __name__ == "__main__":
    unittest.main()
