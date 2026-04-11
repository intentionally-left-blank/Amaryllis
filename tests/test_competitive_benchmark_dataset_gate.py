from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CompetitiveBenchmarkDatasetGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "competitive_benchmark_dataset_gate.py"
        self.dataset = self.repo_root / "eval" / "datasets" / "quality" / "competitive_benchmark_scenarios_v1.json"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_gate_passes_default_dataset(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[competitive-benchmark-dataset-gate] OK", proc.stdout)

    def test_gate_fails_when_vendor_marker_present(self) -> None:
        payload = json.loads(self.dataset.read_text(encoding="utf-8"))
        scenarios = payload.get("scenarios", [])
        self.assertIsInstance(scenarios, list)
        self.assertGreater(len(scenarios), 0)
        scenarios[0]["prompt"] = "Create this with OpenAI specific routing."

        with tempfile.TemporaryDirectory(prefix="amaryllis-competitive-dataset-gate-test-") as tmp:
            dataset_path = Path(tmp) / "dataset.json"
            output_path = Path(tmp) / "report.json"
            dataset_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            proc = self._run("--dataset", str(dataset_path), "--output", str(output_path))
            self.assertEqual(proc.returncode, 1, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[competitive-benchmark-dataset-gate] FAILED", proc.stdout)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(str(report.get("summary", {}).get("status")), "fail")
            self.assertGreater(int(report.get("summary", {}).get("vendor_neutrality_violations", 0)), 0)

    def test_gate_writes_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-competitive-dataset-gate-test-") as tmp:
            output = Path(tmp) / "report.json"
            proc = self._run("--output", str(output))
            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertTrue(output.exists())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(str(payload.get("suite")), "competitive_benchmark_dataset_gate_v1")
            summary = payload.get("summary", {})
            self.assertIsInstance(summary, dict)
            self.assertEqual(str(summary.get("status")), "pass")
            lane_counts = payload.get("lane_counts", {})
            self.assertEqual(int(lane_counts.get("create", 0)), 2)
            self.assertEqual(int(lane_counts.get("schedule", 0)), 2)
            self.assertEqual(int(lane_counts.get("quality", 0)), 2)
            self.assertEqual(int(lane_counts.get("recovery", 0)), 2)


if __name__ == "__main__":
    unittest.main()
