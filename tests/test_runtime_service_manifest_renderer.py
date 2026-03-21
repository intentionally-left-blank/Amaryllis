from __future__ import annotations

import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RuntimeServiceManifestRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "runtime" / "render_service_manifest.py"

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
        self.assertIn("--target", text)
        self.assertIn("--channel", text)
        self.assertIn("--environment", text)
        self.assertIn("--output", text)

    def test_linux_systemd_manifest_contains_expected_fields(self) -> None:
        proc = self._run(
            "--target",
            "linux-systemd",
            "--service-name",
            "amaryllis-runtime",
            "--install-root",
            "/tmp/amaryllis",
            "--bin-dir",
            "/tmp/bin",
            "--channel",
            "canary",
            "--environment",
            "EXTRA_FLAG=1",
        )
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        text = proc.stdout
        self.assertIn("[Unit]", text)
        self.assertIn("Description=Amaryllis Runtime Service (canary)", text)
        self.assertIn("ExecStart=/tmp/bin/amaryllis-runtime", text)
        self.assertIn('Environment="AMARYLLIS_LINUX_RELEASE_CHANNEL=canary"', text)
        self.assertIn('Environment="EXTRA_FLAG=1"', text)
        self.assertIn("WantedBy=default.target", text)

    def test_macos_launchd_manifest_is_valid_plist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-runtime-manifest-test-") as tmp:
            output = Path(tmp) / "runtime.plist"
            proc = self._run(
                "--target",
                "macos-launchd",
                "--service-name",
                "amaryllis-runtime",
                "--install-root",
                "/tmp/amaryllis",
                "--bin-dir",
                "/tmp/bin",
                "--channel",
                "stable",
                "--label-prefix",
                "org.amaryllis",
                "--environment",
                "EXTRA_FLAG=1",
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = plistlib.loads(output.read_bytes())
            self.assertEqual(payload.get("Label"), "org.amaryllis.amaryllis-runtime")
            self.assertEqual(payload.get("ProgramArguments"), ["/tmp/bin/amaryllis-runtime"])
            env = payload.get("EnvironmentVariables")
            self.assertIsInstance(env, dict)
            assert isinstance(env, dict)
            self.assertEqual(env.get("AMARYLLIS_LINUX_RELEASE_CHANNEL"), "stable")
            self.assertEqual(env.get("EXTRA_FLAG"), "1")

    def test_invalid_environment_entry_returns_error(self) -> None:
        proc = self._run(
            "--target",
            "linux-systemd",
            "--environment",
            "BROKEN",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("expected KEY=VALUE", proc.stderr)


if __name__ == "__main__":
    unittest.main()
