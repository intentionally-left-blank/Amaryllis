from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class ToolchainDriftCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "check_toolchain_drift.py"

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

    def _write_fixture_repo(
        self,
        root: Path,
        *,
        python_version: str = "3.11.11",
        swift_tools_version: str = "5.9",
        setup_action: str = "actions/setup-python@v5",
        runner: str = "ubuntu-latest",
        bootstrap_binary: str = "python3.11",
    ) -> Path:
        manifest_path = root / "runtime" / "toolchains" / "core.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        workflow_path = root / ".github" / "workflows" / "gate.yml"
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(
            textwrap.dedent(
                f"""
                name: Gate
                jobs:
                  check:
                    runs-on: {runner}
                    steps:
                      - uses: {setup_action}
                        with:
                          python-version: \"{python_version}\"
                """
            ).lstrip(),
            encoding="utf-8",
        )

        swift_path = root / "macos" / "AmaryllisApp" / "Package.swift"
        swift_path.parent.mkdir(parents=True, exist_ok=True)
        swift_path.write_text(
            textwrap.dedent(
                f"""
                // swift-tools-version: {swift_tools_version}
                import PackageDescription
                let package = Package(name: \"AmaryllisApp\")
                """
            ).lstrip(),
            encoding="utf-8",
        )

        bootstrap_path = root / "scripts" / "bootstrap" / "reproducible_local_bootstrap.sh"
        bootstrap_path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap_path.write_text(
            textwrap.dedent(
                f"""
                #!/usr/bin/env bash
                PYTHON_BIN=\"${{AMARYLLIS_BOOTSTRAP_PYTHON:-{bootstrap_binary}}}\"
                """
            ).lstrip(),
            encoding="utf-8",
        )

        manifest = {
            "schema_version": 1,
            "manifest_version": "fixture",
            "python": {
                "version": python_version,
                "bootstrap_binary": bootstrap_binary,
                "setup_action": setup_action,
            },
            "swift": {"tools_version": swift_tools_version},
            "ci": {
                "runner": runner,
                "workflows": [".github/workflows/gate.yml"],
            },
            "checks": {
                "swift_package_file": "macos/AmaryllisApp/Package.swift",
                "bootstrap_script": "scripts/bootstrap/reproducible_local_bootstrap.sh",
            },
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest_path

    def test_toolchain_drift_check_passes_for_repository_defaults(self) -> None:
        proc = self._run()
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("[toolchain-drift] OK", proc.stdout)

    def test_fails_when_workflow_python_version_drifts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-toolchain-drift-") as tmp:
            tmp_root = Path(tmp)
            self._write_fixture_repo(tmp_root, python_version="3.11.11")

            workflow_path = tmp_root / ".github" / "workflows" / "gate.yml"
            workflow_path.write_text(
                workflow_path.read_text(encoding="utf-8").replace("3.11.11", "3.12.0"),
                encoding="utf-8",
            )

            proc = self._run("--repo-root", str(tmp_root), "--manifest", "runtime/toolchains/core.json")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("python-version drift", proc.stderr)

    def test_fails_when_swift_tools_version_drifts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-toolchain-drift-") as tmp:
            tmp_root = Path(tmp)
            self._write_fixture_repo(tmp_root, swift_tools_version="5.9")

            swift_path = tmp_root / "macos" / "AmaryllisApp" / "Package.swift"
            swift_path.write_text(
                swift_path.read_text(encoding="utf-8").replace("5.9", "5.10"),
                encoding="utf-8",
            )

            proc = self._run("--repo-root", str(tmp_root), "--manifest", "runtime/toolchains/core.json")

        self.assertEqual(proc.returncode, 1)
        self.assertIn("swift-tools-version drift", proc.stderr)

    def test_fails_when_checked_python_executable_does_not_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-toolchain-drift-") as tmp:
            tmp_root = Path(tmp)
            self._write_fixture_repo(tmp_root, python_version="9.9.9")
            proc = self._run(
                "--repo-root",
                str(tmp_root),
                "--manifest",
                "runtime/toolchains/core.json",
                "--check-python-executable",
                sys.executable,
            )

        self.assertEqual(proc.returncode, 1)
        self.assertIn("python executable version drift", proc.stderr)


if __name__ == "__main__":
    unittest.main()
