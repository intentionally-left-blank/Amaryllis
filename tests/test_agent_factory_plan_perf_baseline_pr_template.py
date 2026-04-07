from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AgentFactoryPlanPerfBaselinePrTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = (
            self.repo_root
            / "scripts"
            / "release"
            / "agent_factory_plan_perf_baseline_pr_template.py"
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_generates_markdown_template_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-pr-template-") as tmp:
            tmp_dir = Path(tmp)
            refresh_report = tmp_dir / "refresh-report.json"
            suggested_baseline = tmp_dir / "suggested-baseline.json"
            template_output = tmp_dir / "baseline-pr-template.md"
            metadata_output = tmp_dir / "baseline-pr-template-metadata.json"

            refresh_report.write_text(
                json.dumps(
                    {
                        "suite": "agent_factory_plan_perf_baseline_refresh_v1",
                        "generated_at": "2026-04-08T00:00:00+00:00",
                        "summary": {
                            "status": "warn",
                            "profiles_total": 2,
                            "profiles_warn": 1,
                            "profiles_fail": 0,
                        },
                        "profiles": [
                            {
                                "profile": "release",
                                "status": "pass",
                                "observed": {"p95_latency_ms": 1050.0},
                                "baseline_thresholds": {"max_p95_latency_ms": 1200.0},
                                "suggested_thresholds": {"max_p95_latency_ms": 1250.0},
                                "drift": {"p95_threshold_delta_pct": 4.1667},
                            },
                            {
                                "profile": "nightly",
                                "status": "warn",
                                "observed": {"p95_latency_ms": 1700.0},
                                "baseline_thresholds": {"max_p95_latency_ms": 1500.0},
                                "suggested_thresholds": {"max_p95_latency_ms": 1800.0},
                                "drift": {"p95_threshold_delta_pct": 20.0},
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            suggested_baseline.write_text("{}\n", encoding="utf-8")

            proc = self._run(
                "--refresh-report",
                str(refresh_report),
                "--suggested-baseline",
                str(suggested_baseline),
                "--output",
                str(template_output),
                "--metadata-output",
                str(metadata_output),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(template_output.exists())
            self.assertTrue(metadata_output.exists())

            markdown = template_output.read_text(encoding="utf-8")
            self.assertIn("## Agent Factory Baseline Refresh", markdown)
            self.assertIn("Refresh artifact reference", markdown)
            self.assertIn("## Prefilled change_control", markdown)
            self.assertIn('"manual_approval": true', markdown)
            self.assertIn('"approval_scope": [', markdown)
            self.assertIn("| release |", markdown)
            self.assertIn("| nightly |", markdown)

            metadata = json.loads(metadata_output.read_text(encoding="utf-8"))
            self.assertEqual(
                str(metadata.get("suite") or ""),
                "agent_factory_plan_perf_baseline_pr_template_v1",
            )
            change_control = metadata.get("change_control", {})
            self.assertIsInstance(change_control, dict)
            self.assertEqual(str(change_control.get("ticket") or ""), "P8-A10/P8-A13")
            self.assertTrue(bool(change_control.get("manual_approval")))
            approval_scope = change_control.get("approval_scope", [])
            self.assertIsInstance(approval_scope, list)
            self.assertIn("release", approval_scope)
            self.assertIn("nightly", approval_scope)

    def test_fails_when_refresh_suite_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-pr-template-") as tmp:
            tmp_dir = Path(tmp)
            refresh_report = tmp_dir / "refresh-report-invalid.json"
            template_output = tmp_dir / "baseline-pr-template.md"

            refresh_report.write_text(
                json.dumps({"suite": "wrong_suite"}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            proc = self._run(
                "--refresh-report",
                str(refresh_report),
                "--output",
                str(template_output),
            )
            self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("unexpected_refresh_suite", proc.stderr)


if __name__ == "__main__":
    unittest.main()
