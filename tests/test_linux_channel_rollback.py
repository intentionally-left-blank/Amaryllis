from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LinuxChannelRollbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "linux_channel_rollback.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def _touch_release(self, install_root: Path, release_id: str) -> Path:
        release = install_root / "releases" / release_id
        (release / "src" / "runtime").mkdir(parents=True, exist_ok=True)
        (release / "src" / "runtime" / "server.py").write_text("# smoke\n", encoding="utf-8")
        return release

    def test_help_contract(self) -> None:
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        text = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("--install-root", text)
        self.assertIn("--channel", text)
        self.assertIn("--steps", text)
        self.assertIn("--dry-run", text)

    def test_rejects_invalid_channel(self) -> None:
        proc = self._run("--channel", "beta")
        self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("invalid --channel", proc.stderr)

    def test_rolls_back_canary_and_preserves_current(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-linux-channel-rollback-") as tmp:
            install_root = Path(tmp) / "install"
            channels_dir = install_root / "channels"
            channels_dir.mkdir(parents=True, exist_ok=True)
            release_one = self._touch_release(install_root, "r1")
            release_two = self._touch_release(install_root, "r2")
            stable_release = self._touch_release(install_root, "stable-r1")

            (channels_dir / "canary").symlink_to(release_two)
            (channels_dir / "canary.history").write_text("r1\nr2\n", encoding="utf-8")
            (install_root / "current").symlink_to(stable_release)

            proc = self._run(
                "--install-root",
                str(install_root),
                "--channel",
                "canary",
                "--steps",
                "1",
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertEqual((channels_dir / "canary").resolve(), release_one.resolve())
            self.assertEqual((install_root / "current").resolve(), stable_release.resolve())
            history = (channels_dir / "canary.history").read_text(encoding="utf-8").splitlines()
            self.assertEqual(history[-1], "r1")

    def test_rolls_back_stable_and_updates_current(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-linux-channel-rollback-") as tmp:
            install_root = Path(tmp) / "install"
            channels_dir = install_root / "channels"
            channels_dir.mkdir(parents=True, exist_ok=True)
            release_one = self._touch_release(install_root, "s1")
            release_two = self._touch_release(install_root, "s2")

            (channels_dir / "stable").symlink_to(release_two)
            (channels_dir / "stable.history").write_text("s1\ns2\n", encoding="utf-8")
            (install_root / "current").symlink_to(release_two)

            proc = self._run(
                "--install-root",
                str(install_root),
                "--channel",
                "stable",
                "--steps",
                "1",
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertEqual((channels_dir / "stable").resolve(), release_one.resolve())
            self.assertEqual((install_root / "current").resolve(), release_one.resolve())
            history = (channels_dir / "stable.history").read_text(encoding="utf-8").splitlines()
            self.assertEqual(history[-1], "s1")


if __name__ == "__main__":
    unittest.main()
