from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest


class LinuxInstallerScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "install_linux.sh"

    def test_script_exists_and_is_shell_valid(self) -> None:
        self.assertTrue(self.script.exists(), "install_linux.sh must exist")
        self.assertTrue(os.access(self.script, os.X_OK), "install_linux.sh must be executable")
        subprocess.run(
            ["bash", "-n", str(self.script)],
            cwd=str(self.repo_root),
            check=True,
        )

    def test_help_contract(self) -> None:
        completed = subprocess.run(
            [str(self.script), "--help"],
            cwd=str(self.repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        text = (completed.stdout or "") + (completed.stderr or "")
        self.assertIn("--release-id", text)
        self.assertIn("--channel", text)
        self.assertIn("--dry-run", text)
        self.assertIn("AMARYLLIS_LINUX_INSTALL_ROOT", text)
        self.assertIn("AMARYLLIS_LINUX_RELEASE_CHANNEL", text)

    @unittest.skipUnless(platform.system() == "Linux", "dry-run install check is Linux-only")
    def test_dry_run_install_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-linux-installer-test-") as tmp:
            root = Path(tmp) / "runtime"
            bin_dir = Path(tmp) / "bin"
            env = dict(os.environ)
            env["AMARYLLIS_LINUX_INSTALL_ROOT"] = str(root)
            env["AMARYLLIS_LINUX_BIN_DIR"] = str(bin_dir)
            env["AMARYLLIS_KEEP_RELEASES"] = "2"

            subprocess.run(
                [
                    str(self.script),
                    "--dry-run",
                    "--release-id",
                    "test-release",
                ],
                cwd=str(self.repo_root),
                env=env,
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
