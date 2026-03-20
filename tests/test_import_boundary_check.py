from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class ImportBoundaryCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "check_import_boundaries.py"

    def _run(self, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory(prefix="amaryllis-import-boundary-") as tmp:
            tmp_path = Path(tmp)
            for rel_path, content in files.items():
                target = tmp_path / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(self.script),
                    "--repo-root",
                    str(tmp_path),
                ],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=False,
            )

    def test_passes_when_boundaries_are_respected(self) -> None:
        proc = self._run(
            {
                "agents/runner.py": "from storage.database import Database\n",
                "api/chat.py": "from runtime.auth import auth_context_from_request\n",
                "kernel/contracts.py": "from typing import Any\n",
                "storage/repo.py": "from storage.migrations import apply_migrations\n",
            }
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("import boundary check OK", proc.stdout)

    def test_fails_for_api_to_storage_import(self) -> None:
        proc = self._run(
            {
                "api/chat.py": "from storage.database import Database\n",
            }
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("forbidden import api->storage", proc.stderr)

    def test_fails_for_orchestration_to_api_import(self) -> None:
        proc = self._run(
            {
                "tasks/executor.py": "from api.chat_api import router\n",
            }
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("forbidden import orchestration->api", proc.stderr)

    def test_fails_for_api_to_orchestration_import(self) -> None:
        proc = self._run(
            {
                "api/chat.py": "from tasks.task_executor import TaskExecutor\n",
            }
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("forbidden import api->orchestration", proc.stderr)

    def test_fails_for_storage_to_orchestration_import(self) -> None:
        proc = self._run(
            {
                "storage/repo.py": "from agents.agent import Agent\n",
            }
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("forbidden import storage->orchestration", proc.stderr)

    def test_fails_for_kernel_to_orchestration_import(self) -> None:
        proc = self._run(
            {
                "kernel/contracts.py": "from agents.agent import Agent\n",
            }
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("forbidden import kernel->orchestration", proc.stderr)


if __name__ == "__main__":
    unittest.main()
