from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class NewsMissionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "news_mission_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_news_mission_gate_passes_default(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[news-mission-gate] OK", proc.stdout)

    def test_news_mission_gate_fails_when_min_sections_is_impossible(self) -> None:
        proc = self._run("--min-sections", "99")
        self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[news-mission-gate] FAILED", proc.stdout)
        self.assertIn("section_count_below_min", proc.stdout)

    def test_news_mission_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-news-gate-test-") as tmp:
            output = Path(tmp) / "report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "news_mission_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")


if __name__ == "__main__":
    unittest.main()

