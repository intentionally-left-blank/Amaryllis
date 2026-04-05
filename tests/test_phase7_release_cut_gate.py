from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class Phase7ReleaseCutGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "phase7_release_cut_gate.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(self.script), *args]
        return subprocess.run(
            command,
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_phase7_release_cut_gate_passes_with_valid_reports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-phase7-release-cut-gate-") as tmp:
            base = Path(tmp)
            news = base / "news.json"
            provider = base / "provider.json"
            mission = base / "mission.json"
            mission_pack = base / "mission-pack.json"
            output = base / "out.json"

            self._write_json(
                news,
                {
                    "suite": "news_mission_gate_v1",
                    "summary": {"status": "pass", "checks_failed": 0},
                },
            )
            self._write_json(
                provider,
                {
                    "suite": "provider_session_policy_check_v1",
                    "summary": {"status": "pass", "checks_failed": 0},
                },
            )
            self._write_json(
                mission,
                {
                    "suite": "mission_success_recovery_report_pack_v2",
                    "summary": {"status": "pass", "checks_failed": 0},
                    "kpis": {
                        "news_citation_coverage_rate": 1.0,
                        "news_mission_success_rate_pct": 100.0,
                        "mission_success_rate_pct": 100.0,
                    },
                },
            )
            self._write_json(
                mission_pack,
                {
                    "suite": "mission_report_pack_gate_v1",
                    "summary": {"status": "pass"},
                },
            )

            proc = self._run(
                "--news-report",
                str(news),
                "--provider-report",
                str(provider),
                "--mission-report",
                str(mission),
                "--mission-pack-gate-report",
                str(mission_pack),
                "--output",
                str(output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[phase7-release-cut-gate] OK", proc.stdout)
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "phase7_release_cut_gate_v1")
            self.assertEqual(str(payload.get("summary", {}).get("status")), "pass")

    def test_phase7_release_cut_gate_fails_when_threshold_is_not_met(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-phase7-release-cut-gate-") as tmp:
            base = Path(tmp)
            news = base / "news.json"
            provider = base / "provider.json"
            mission = base / "mission.json"
            mission_pack = base / "mission-pack.json"

            self._write_json(news, {"suite": "news_mission_gate_v1", "summary": {"status": "pass", "checks_failed": 0}})
            self._write_json(
                provider,
                {"suite": "provider_session_policy_check_v1", "summary": {"status": "pass", "checks_failed": 0}},
            )
            self._write_json(
                mission,
                {
                    "suite": "mission_success_recovery_report_pack_v2",
                    "summary": {"status": "pass", "checks_failed": 0},
                    "kpis": {
                        "news_citation_coverage_rate": 0.5,
                        "news_mission_success_rate_pct": 100.0,
                        "mission_success_rate_pct": 100.0,
                    },
                },
            )
            self._write_json(mission_pack, {"suite": "mission_report_pack_gate_v1", "summary": {"status": "pass"}})

            proc = self._run(
                "--news-report",
                str(news),
                "--provider-report",
                str(provider),
                "--mission-report",
                str(mission),
                "--mission-pack-gate-report",
                str(mission_pack),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[phase7-release-cut-gate] FAILED", proc.stdout)
            self.assertIn("kpi.news_citation_coverage_rate", proc.stdout)

    def test_phase7_release_cut_gate_fails_when_report_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-phase7-release-cut-gate-") as tmp:
            base = Path(tmp)
            missing = base / "missing.json"
            proc = self._run(
                "--news-report",
                str(missing),
                "--provider-report",
                str(missing),
                "--mission-report",
                str(missing),
                "--mission-pack-gate-report",
                str(missing),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[phase7-release-cut-gate] FAILED", proc.stdout)
            self.assertIn("news_report.exists", proc.stdout)


if __name__ == "__main__":
    unittest.main()
