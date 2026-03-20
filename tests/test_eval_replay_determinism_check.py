from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


class EvalReplayDeterminismCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "check_eval_replay_determinism.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_eval_replay_determinism_check_passes_with_default_fixtures(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[eval-replay-determinism] OK", proc.stdout)

    def test_eval_replay_determinism_check_fails_when_fixture_path_missing(self) -> None:
        proc = self._run("--golden-fixture-responses", "eval/fixtures/golden_tasks/missing.json")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("golden fixture responses not found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
