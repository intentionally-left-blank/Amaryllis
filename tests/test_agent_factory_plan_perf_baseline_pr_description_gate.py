from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentFactoryPlanPerfBaselinePrDescriptionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = (
            self.repo_root
            / "scripts"
            / "release"
            / "agent_factory_plan_perf_baseline_pr_description_gate.py"
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_gate_passes_with_artifact_reference_and_approver_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-pr-body-") as tmp:
            tmp_dir = Path(tmp)
            event_path = tmp_dir / "event.json"
            output_path = tmp_dir / "report.json"

            event_path.write_text(
                json.dumps(
                    {
                        "number": 42,
                        "pull_request": {
                            "title": "Refresh Agent Factory perf baseline",
                            "body": (
                                "## Agent Factory Baseline Refresh\n"
                                "- Refresh artifact reference: https://github.com/example/repo/actions/runs/123456789\n"
                                "- Approver identity: @tier1-maintainer\n"
                            ),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            proc = self._run(
                "--event-path",
                str(event_path),
                "--output",
                str(output_path),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output_path.exists())
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status") or ""), "pass")

    def test_gate_fails_when_approver_identity_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-pr-body-") as tmp:
            tmp_dir = Path(tmp)
            event_path = tmp_dir / "event.json"
            output_path = tmp_dir / "report.json"

            event_path.write_text(
                json.dumps(
                    {
                        "number": 43,
                        "pull_request": {
                            "title": "Refresh Agent Factory perf baseline",
                            "body": (
                                "## Agent Factory Baseline Refresh\n"
                                "- Refresh artifact reference: artifacts/agent-factory-plan-perf-baseline-refresh-report.json\n"
                                "- Approver identity: <@github-handle>\n"
                            ),
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            proc = self._run(
                "--event-path",
                str(event_path),
                "--output",
                str(output_path),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status") or ""), "fail")

    def test_gate_skips_non_pr_event_when_opted_in(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-pr-body-") as tmp:
            tmp_dir = Path(tmp)
            event_path = tmp_dir / "event.json"
            output_path = tmp_dir / "report.json"

            event_path.write_text(
                json.dumps({"workflow": "dispatch"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            proc = self._run(
                "--event-path",
                str(event_path),
                "--allow-non-pr-events",
                "--output",
                str(output_path),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            summary = payload.get("summary", {})
            self.assertEqual(str(summary.get("status") or ""), "skip")


if __name__ == "__main__":
    unittest.main()
