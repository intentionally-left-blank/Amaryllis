from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class ReleaseProvenanceGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.script = self.repo_root / "scripts" / "release" / "generate_release_provenance.py"

    def _run(
        self,
        *args: str,
        cwd: Path,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def _init_fixture_repo(self, root: Path, *, lock_line: str = "fastapi==0.135.1") -> Path:
        (root / "runtime" / "toolchains").mkdir(parents=True, exist_ok=True)
        (root / "runtime" / "profiles").mkdir(parents=True, exist_ok=True)
        (root / "slo_profiles").mkdir(parents=True, exist_ok=True)
        (root / "build").mkdir(parents=True, exist_ok=True)

        (root / "requirements.lock").write_text(
            textwrap.dedent(
                f"""
                # deterministic lock
                {lock_line}
                uvicorn==0.41.0 ; platform_system == \"Linux\"
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

        (root / "runtime" / "toolchains" / "core.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "manifest_version": "fixture",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "runtime" / "profiles" / "dev.json").write_text('{"name": "dev"}\n', encoding="utf-8")
        (root / "runtime" / "profiles" / "ci.json").write_text('{"name": "ci"}\n', encoding="utf-8")
        (root / "slo_profiles" / "dev.json").write_text('{"name": "dev"}\n', encoding="utf-8")
        (root / "build" / "candidate.bin").write_bytes(b"amaryllis-release-candidate")

        subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "ci@example.test"], cwd=str(root), check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "CI Test"], cwd=str(root), check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=str(root), check=True, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", "fixture"], cwd=str(root), check=True, capture_output=True, text=True)

        return root / "build" / "candidate.bin"

    def test_generates_signed_release_provenance_and_sbom(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-release-provenance-") as tmp:
            tmp_root = Path(tmp)
            artifact_path = self._init_fixture_repo(tmp_root)

            proc = self._run(
                "--repo-root",
                str(tmp_root),
                "--artifact",
                str(artifact_path),
                "--generated-at",
                "2026-03-20T12:00:00Z",
                cwd=tmp_root,
                env_overrides={
                    "AMARYLLIS_PROVENANCE_SIGNING_KEY": "fixture-secret-key",
                    "AMARYLLIS_PROVENANCE_KEY_ID": "fixture-kid",
                },
            )

            self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
            self.assertIn("[release-provenance] OK", proc.stdout)

            sbom_path = tmp_root / "artifacts" / "release-sbom.json"
            provenance_path = tmp_root / "artifacts" / "release-provenance.json"
            signature_path = tmp_root / "artifacts" / "release-provenance.sig"
            self.assertTrue(sbom_path.exists())
            self.assertTrue(provenance_path.exists())
            self.assertTrue(signature_path.exists())

            sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
            self.assertEqual(sbom["schema_version"], 1)
            self.assertEqual(sbom["dependency_count"], 2)

            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            self.assertEqual(provenance["signature"]["algorithm"], "hmac-sha256")
            self.assertEqual(provenance["signature"]["key_id"], "fixture-kid")
            self.assertEqual(provenance["signature"]["trust_level"], "managed")
            self.assertIn("materials", provenance)
            self.assertTrue(any(item["path"].endswith("build/candidate.bin") for item in provenance["materials"]))

            unsigned_payload = dict(provenance)
            unsigned_payload["signature"] = {}
            canonical = json.dumps(unsigned_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            expected_signature = hmac.new(
                b"fixture-secret-key",
                canonical.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            self.assertEqual(provenance["signature"]["value"], expected_signature)
            self.assertEqual(signature_path.read_text(encoding="utf-8").strip(), expected_signature)

    def test_fails_without_signing_key_when_required(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-release-provenance-") as tmp:
            tmp_root = Path(tmp)
            artifact_path = self._init_fixture_repo(tmp_root)

            proc = self._run(
                "--repo-root",
                str(tmp_root),
                "--artifact",
                str(artifact_path),
                "--require-signing-key",
                cwd=tmp_root,
                env_overrides={
                    "AMARYLLIS_PROVENANCE_SIGNING_KEY": "",
                    "AMARYLLIS_PROVENANCE_KEY_ID": "",
                },
            )

            self.assertEqual(proc.returncode, 1)
            self.assertIn("missing signing key", proc.stderr)

    def test_fails_for_unpinned_lock_entries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-release-provenance-") as tmp:
            tmp_root = Path(tmp)
            artifact_path = self._init_fixture_repo(tmp_root, lock_line="fastapi>=0.135.1")

            proc = self._run(
                "--repo-root",
                str(tmp_root),
                "--artifact",
                str(artifact_path),
                cwd=tmp_root,
                env_overrides={
                    "AMARYLLIS_PROVENANCE_SIGNING_KEY": "fixture-secret-key",
                },
            )

            self.assertEqual(proc.returncode, 1)
            self.assertIn("must be pinned with ==", proc.stderr)


if __name__ == "__main__":
    unittest.main()
