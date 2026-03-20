from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class NightlySLOBurnRateGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "nightly_slo_burn_rate_gate.py"

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
    def _write_report(path: Path, *, request_values: list[float], run_values: list[float]) -> None:
        samples: list[dict[str, float | int]] = []
        for index, (request_value, run_value) in enumerate(zip(request_values, run_values), start=1):
            samples.append(
                {
                    "round": index,
                    "request_burn_rate": float(request_value),
                    "run_burn_rate": float(run_value),
                    "request_budget": 0.6,
                    "run_budget": 0.6,
                    "request_samples": 10,
                    "run_samples": 10,
                }
            )
        payload = {
            "suite": "nightly_reliability_smoke_v1",
            "burn_rate": {
                "samples": samples,
                "summary": {
                    "sample_count": len(samples),
                    "request": {"budget": 0.6},
                    "runs": {"budget": 0.6},
                },
            },
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_gate_passes_when_breach_is_not_sustained(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-nightly-burn-gate-") as tmp:
            report_path = Path(tmp) / "nightly-report.json"
            gate_report_path = Path(tmp) / "burn-gate-report.json"
            self._write_report(
                report_path,
                request_values=[0.2, 0.8, 0.2, 0.2],
                run_values=[0.1, 0.1, 0.1, 0.1],
            )
            proc = self._run(
                "--report",
                str(report_path),
                "--output",
                str(gate_report_path),
            )
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[nightly-burn-rate] OK", proc.stdout)
            self.assertTrue(gate_report_path.exists())

    def test_gate_fails_on_sustained_burn_rate_breach(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-nightly-burn-gate-") as tmp:
            report_path = Path(tmp) / "nightly-report.json"
            gate_report_path = Path(tmp) / "burn-gate-report.json"
            self._write_report(
                report_path,
                request_values=[0.7, 0.75, 0.8, 0.2],
                run_values=[0.1, 0.1, 0.1, 0.1],
            )
            proc = self._run(
                "--report",
                str(report_path),
                "--output",
                str(gate_report_path),
            )
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[nightly-burn-rate] FAILED", proc.stdout)
            report = json.loads(gate_report_path.read_text(encoding="utf-8"))
            self.assertFalse(bool(report.get("passed")))
            self.assertGreaterEqual(len(report.get("failures", [])), 1)

    def test_gate_fails_for_missing_burn_rate_section(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-nightly-burn-gate-") as tmp:
            report_path = Path(tmp) / "nightly-report.json"
            report_path.write_text('{"suite":"nightly_reliability_smoke_v1"}\n', encoding="utf-8")
            proc = self._run("--report", str(report_path))
            self.assertEqual(proc.returncode, 2, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("missing burn_rate section", proc.stderr)


if __name__ == "__main__":
    unittest.main()
