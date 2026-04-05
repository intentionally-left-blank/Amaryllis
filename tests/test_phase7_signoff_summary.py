from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class Phase7SignoffSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "build_phase7_signoff_summary.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_summary_build_passes_and_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-phase7-signoff-summary-") as tmp:
            base = Path(tmp)
            phase7 = base / "phase7.json"
            news = base / "news.json"
            provider = base / "provider.json"
            mission = base / "mission.json"
            output = base / "summary.json"
            markdown = base / "summary.md"

            self._write_json(
                phase7,
                {
                    "suite": "phase7_release_cut_gate_v1",
                    "summary": {"status": "pass", "checks_total": 10, "checks_failed": 0},
                    "checks": [{"name": "a", "ok": True, "detail": "ok"}],
                },
            )
            self._write_json(news, {"summary": {"status": "pass"}})
            self._write_json(provider, {"summary": {"status": "pass"}})
            self._write_json(
                mission,
                {
                    "summary": {"status": "pass"},
                    "kpis": {
                        "news_citation_coverage_rate": 1.0,
                        "news_mission_success_rate_pct": 100.0,
                        "mission_success_rate_pct": 99.5,
                    },
                },
            )

            proc = self._run(
                "--phase7-gate-report",
                str(phase7),
                "--news-report",
                str(news),
                "--provider-report",
                str(provider),
                "--mission-report",
                str(mission),
                "--output",
                str(output),
                "--markdown-output",
                str(markdown),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[phase7-signoff-summary] OK", proc.stdout)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "phase7_signoff_summary_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")
            self.assertTrue(markdown.exists())

    def test_summary_build_fails_when_phase7_status_failed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-phase7-signoff-summary-") as tmp:
            base = Path(tmp)
            phase7 = base / "phase7.json"
            news = base / "news.json"
            provider = base / "provider.json"
            mission = base / "mission.json"
            output = base / "summary.json"

            self._write_json(
                phase7,
                {
                    "suite": "phase7_release_cut_gate_v1",
                    "summary": {"status": "fail", "checks_total": 10, "checks_failed": 1},
                    "checks": [{"name": "kpi.news", "ok": False, "detail": "below threshold"}],
                },
            )
            self._write_json(news, {"summary": {"status": "pass"}})
            self._write_json(provider, {"summary": {"status": "pass"}})
            self._write_json(
                mission,
                {
                    "summary": {"status": "pass"},
                    "kpis": {
                        "news_citation_coverage_rate": 0.5,
                        "news_mission_success_rate_pct": 95.0,
                        "mission_success_rate_pct": 98.0,
                    },
                },
            )

            proc = self._run(
                "--phase7-gate-report",
                str(phase7),
                "--news-report",
                str(news),
                "--provider-report",
                str(provider),
                "--mission-report",
                str(mission),
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[phase7-signoff-summary] FAILED", proc.stdout)
            self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
