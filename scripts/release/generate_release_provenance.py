#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any


_REQUIREMENT_COMPARATOR_RE = re.compile(r"(===|==|~=|!=|<=|>=|<|>)")
_DEFAULT_SIGNING_KEY = "amaryllis-dev-provenance-key"


class ReleaseProvenanceError(ValueError):
    pass


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate signed release provenance and deterministic dependency inventory (SBOM) "
            "for reproducible release gates."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--requirements-lock", default="requirements.lock", help="Path to lock requirements")
    parser.add_argument("--toolchain-manifest", default="runtime/toolchains/core.json", help="Path to toolchain manifest")
    parser.add_argument("--runtime-profiles-dir", default="runtime/profiles", help="Path to runtime profiles directory")
    parser.add_argument("--slo-profiles-dir", default="slo_profiles", help="Path to SLO profiles directory")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Additional release artifact path to hash (repeatable)",
    )
    parser.add_argument(
        "--sbom-output",
        default="artifacts/release-sbom.json",
        help="Output path for dependency inventory JSON",
    )
    parser.add_argument(
        "--provenance-output",
        default="artifacts/release-provenance.json",
        help="Output path for release provenance JSON",
    )
    parser.add_argument(
        "--signature-output",
        default="artifacts/release-provenance.sig",
        help="Output path for detached provenance signature",
    )
    parser.add_argument(
        "--signing-key-env",
        default="AMARYLLIS_PROVENANCE_SIGNING_KEY",
        help="Environment variable name that stores the provenance signing key",
    )
    parser.add_argument(
        "--signing-key-id-env",
        default="AMARYLLIS_PROVENANCE_KEY_ID",
        help="Environment variable name that stores the key identifier",
    )
    parser.add_argument(
        "--require-signing-key",
        action="store_true",
        help="Fail when signing key environment variable is missing",
    )
    parser.add_argument(
        "--generated-at",
        default="",
        help="Optional fixed RFC3339 timestamp for deterministic tests",
    )
    return parser.parse_args()


def _resolve_path(repo_root: Path, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest(), path.stat().st_size


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _normalize_requirement_name(raw: str) -> str:
    base = raw.strip()
    if not base:
        return ""
    token = _REQUIREMENT_COMPARATOR_RE.split(base, maxsplit=1)[0].strip().lower()
    return token


def _parse_locked_dependencies(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise ReleaseProvenanceError(f"requirements lock not found: {path}")

    dependencies: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue

        requirement_part = line.split(";", 1)[0].strip()
        marker_part = ""
        if ";" in line:
            marker_part = line.split(";", 1)[1].strip()

        if "==" not in requirement_part:
            raise ReleaseProvenanceError(
                f"requirements lock entry must be pinned with == for SBOM reproducibility: {line}"
            )

        package_name = _normalize_requirement_name(requirement_part)
        if not package_name:
            raise ReleaseProvenanceError(f"could not parse lock entry: {line}")

        if package_name in seen:
            raise ReleaseProvenanceError(f"duplicate lock entry for package '{package_name}'")

        version = requirement_part.split("==", 1)[1].strip()
        if not version:
            raise ReleaseProvenanceError(f"invalid pinned version in lock entry: {line}")

        seen.add(package_name)
        record = {
            "name": package_name,
            "version": version,
            "specifier": requirement_part,
        }
        if marker_part:
            record["marker"] = marker_part
        dependencies.append(record)

    if not dependencies:
        raise ReleaseProvenanceError(f"requirements lock is empty: {path}")

    dependencies.sort(key=lambda item: (item["name"], item["version"]))
    return dependencies


def _iter_json_files(directory: Path) -> list[Path]:
    if not directory.exists() or not directory.is_dir():
        raise ReleaseProvenanceError(f"expected directory does not exist: {directory}")
    files = sorted(path.resolve() for path in directory.glob("*.json") if path.is_file())
    if not files:
        raise ReleaseProvenanceError(f"no JSON files found in directory: {directory}")
    return files


def _display_path(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repo_root))
    except ValueError:
        return str(resolved)


def _build_sbom_payload(
    *,
    generated_at: str,
    dependencies: list[dict[str, str]],
    lockfile_display_path: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "format": "amaryllis.sbom.v1",
        "generated_at": generated_at,
        "source": {
            "lockfile": lockfile_display_path,
        },
        "dependency_count": len(dependencies),
        "dependencies": dependencies,
    }


def _build_digest_records(repo_root: Path, files: list[Path]) -> list[dict[str, Any]]:
    unique_files = sorted({item.resolve() for item in files})
    records: list[dict[str, Any]] = []
    for file_path in unique_files:
        if not file_path.exists() or not file_path.is_file():
            raise ReleaseProvenanceError(f"artifact for digest is missing: {file_path}")
        digest_hex, size_bytes = _hash_file(file_path)
        display_path = _display_path(repo_root, file_path)
        records.append(
            {
                "path": display_path,
                "sha256": digest_hex,
                "size_bytes": size_bytes,
            }
        )
    records.sort(key=lambda item: str(item["path"]))
    return records


def _resolve_generated_at(raw: str) -> str:
    if raw.strip():
        return raw.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"[release-provenance] FAILED: repo root not found: {repo_root}", file=sys.stderr)
        return 2

    try:
        generated_at = _resolve_generated_at(args.generated_at)

        requirements_lock_path = _resolve_path(repo_root, args.requirements_lock)
        toolchain_manifest_path = _resolve_path(repo_root, args.toolchain_manifest)
        runtime_profiles_dir = _resolve_path(repo_root, args.runtime_profiles_dir)
        slo_profiles_dir = _resolve_path(repo_root, args.slo_profiles_dir)

        dependencies = _parse_locked_dependencies(requirements_lock_path)

        commit_sha = _run_git(repo_root, "rev-parse", "HEAD")
        branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        tag = _run_git(repo_root, "describe", "--tags", "--exact-match")
        dirty = bool(_run_git(repo_root, "status", "--porcelain"))

        sbom_output_path = _resolve_path(repo_root, args.sbom_output)
        sbom_output_path.parent.mkdir(parents=True, exist_ok=True)
        sbom_payload = _build_sbom_payload(
            generated_at=generated_at,
            dependencies=dependencies,
            lockfile_display_path=_display_path(repo_root, requirements_lock_path),
        )
        sbom_output_path.write_text(json.dumps(sbom_payload, indent=2) + "\n", encoding="utf-8")
        sbom_sha256, sbom_size = _hash_file(sbom_output_path)

        tracked_files: list[Path] = [
            requirements_lock_path,
            toolchain_manifest_path,
            *_iter_json_files(runtime_profiles_dir),
            *_iter_json_files(slo_profiles_dir),
            sbom_output_path,
        ]

        for raw in args.artifact:
            tracked_files.append(_resolve_path(repo_root, raw))

        digest_records = _build_digest_records(repo_root, tracked_files)

        signing_key = os.environ.get(args.signing_key_env, "").strip()
        key_id = os.environ.get(args.signing_key_id_env, "").strip() or "local-dev"
        trust_level = "managed"

        if not signing_key:
            if args.require_signing_key:
                raise ReleaseProvenanceError(
                    f"missing signing key in env '{args.signing_key_env}' (required)"
                )
            signing_key = _DEFAULT_SIGNING_KEY
            trust_level = "development"

        provenance_payload: dict[str, Any] = {
            "schema_version": 1,
            "format": "amaryllis.release_provenance.v1",
            "generated_at": generated_at,
            "repository": {
                "root": ".",
                "commit": commit_sha or "unknown",
                "branch": branch or "unknown",
                "tag": tag,
                "dirty": dirty,
            },
            "build_environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "system": platform.system(),
                "machine": platform.machine(),
            },
            "sbom": {
                "path": _display_path(repo_root, sbom_output_path),
                "sha256": sbom_sha256,
                "size_bytes": sbom_size,
                "dependency_count": len(dependencies),
            },
            "materials": digest_records,
            "signature": {},
        }

        unsigned_payload = dict(provenance_payload)
        unsigned_payload["signature"] = {}
        canonical = _canonical_json(unsigned_payload)
        signature = hmac.new(
            signing_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        provenance_payload["signature"] = {
            "algorithm": "hmac-sha256",
            "key_id": key_id,
            "trust_level": trust_level,
            "value": signature,
        }

        provenance_output_path = _resolve_path(repo_root, args.provenance_output)
        provenance_output_path.parent.mkdir(parents=True, exist_ok=True)
        provenance_output_path.write_text(
            json.dumps(provenance_payload, indent=2) + "\n",
            encoding="utf-8",
        )

        signature_output_path = _resolve_path(repo_root, args.signature_output)
        signature_output_path.parent.mkdir(parents=True, exist_ok=True)
        signature_output_path.write_text(signature + "\n", encoding="utf-8")

    except ReleaseProvenanceError as exc:
        print(f"[release-provenance] FAILED: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"[release-provenance] FAILED: subprocess error: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        "[release-provenance] OK: "
        f"sbom={sbom_output_path} provenance={provenance_output_path} signature={signature_output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
