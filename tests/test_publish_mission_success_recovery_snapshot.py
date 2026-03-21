from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PublishMissionSuccessRecoverySnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = (
            self.repo_root / "scripts" / "release" / "publish_mission_success_recovery_snapshot.py"
        )

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_help_contract(self) -> None:
        proc = self._run("--help")
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        text = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn("--report", text)
        self.assertIn("--channel", text)
        self.assertIn("--expect-scope", text)
        self.assertIn("--install-root", text)

    def test_publish_nightly_report_to_default_install_root_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-publish-") as tmp:
            base = Path(tmp)
            report = base / "nightly-report.json"
            install_root = base / "install-root"
            self._write_json(
                report,
                {
                    "suite": "mission_success_recovery_report_pack_v2",
                    "scope": "nightly",
                    "summary": {"status": "pass"},
                    "kpis": {"nightly_success_rate_pct": 100.0},
                },
            )

            proc = self._run(
                "--report",
                str(report),
                "--expect-scope",
                "nightly",
                "--install-root",
                str(install_root),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            target = install_root / "observability" / "nightly-mission-success-recovery-latest.json"
            self.assertTrue(target.exists())
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("scope")), "nightly")

    def test_scope_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-mission-report-publish-") as tmp:
            report = Path(tmp) / "release-report.json"
            self._write_json(
                report,
                {
                    "suite": "mission_success_recovery_report_pack_v2",
                    "scope": "release",
                    "summary": {"status": "pass"},
                    "kpis": {},
                },
            )
            proc = self._run(
                "--report",
                str(report),
                "--expect-scope",
                "nightly",
            )
            self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("scope mismatch", proc.stderr.lower())


if __name__ == "__main__":
    unittest.main()
