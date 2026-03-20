#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys


class ToolchainManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ToolchainManifest:
    schema_version: int
    manifest_version: str
    python_version: str
    python_bootstrap_binary: str
    python_setup_action: str
    swift_tools_version: str
    ci_runner: str
    ci_workflows: tuple[str, ...]
    swift_package_file: str
    bootstrap_script: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate reproducibility toolchain manifest drift across CI workflows, "
            "Swift package config, and bootstrap script."
        )
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path.",
    )
    parser.add_argument(
        "--manifest",
        default="runtime/toolchains/core.json",
        help="Path to toolchain manifest JSON.",
    )
    parser.add_argument(
        "--check-python-executable",
        default="",
        help=(
            "Optional python executable to validate against manifest python.version "
            "(for deterministic local bootstrap checks)."
        ),
    )
    return parser.parse_args()


def _resolve_path(repo_root: Path, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise ToolchainManifestError(f"manifest not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolchainManifestError(f"invalid JSON in manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ToolchainManifestError(f"manifest {path} must contain a JSON object")
    return payload


def _require_object(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ToolchainManifestError(f"manifest field '{key}' must be an object")
    return value


def _require_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolchainManifestError(f"manifest field '{key}' must be a non-empty string")
    return value.strip()


def _require_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ToolchainManifestError(f"manifest field '{key}' must be an integer")
    return int(value)


def _require_string_list(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ToolchainManifestError(f"manifest field '{key}' must be a non-empty list")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ToolchainManifestError(f"manifest field '{key}' must contain non-empty strings")
        items.append(item.strip())
    return tuple(items)


def _load_manifest(path: Path) -> ToolchainManifest:
    payload = _load_json(path)
    schema_version = _require_int(payload, "schema_version")
    if schema_version != 1:
        raise ToolchainManifestError(f"unsupported schema_version={schema_version} (expected 1)")

    python_cfg = _require_object(payload, "python")
    swift_cfg = _require_object(payload, "swift")
    ci_cfg = _require_object(payload, "ci")
    checks_cfg = _require_object(payload, "checks")

    python_version = _require_string(python_cfg, "version")
    if re.fullmatch(r"\d+\.\d+\.\d+", python_version) is None:
        raise ToolchainManifestError("python.version must be in major.minor.patch format")

    return ToolchainManifest(
        schema_version=schema_version,
        manifest_version=_require_string(payload, "manifest_version"),
        python_version=python_version,
        python_bootstrap_binary=_require_string(python_cfg, "bootstrap_binary"),
        python_setup_action=_require_string(python_cfg, "setup_action"),
        swift_tools_version=_require_string(swift_cfg, "tools_version"),
        ci_runner=_require_string(ci_cfg, "runner"),
        ci_workflows=_require_string_list(ci_cfg, "workflows"),
        swift_package_file=_require_string(checks_cfg, "swift_package_file"),
        bootstrap_script=_require_string(checks_cfg, "bootstrap_script"),
    )


def _check_workflow(path: Path, manifest: ToolchainManifest) -> list[str]:
    if not path.exists():
        return [f"workflow not found: {path}"]

    text = path.read_text(encoding="utf-8")
    failures: list[str] = []

    runners = re.findall(r"^\s*runs-on:\s*['\"]?([^'\"\s#]+)['\"]?", text, flags=re.MULTILINE)
    if not runners:
        failures.append(f"{path}: runs-on entries are missing")
    else:
        bad_runners = sorted({item for item in runners if item != manifest.ci_runner})
        if bad_runners:
            joined = ", ".join(bad_runners)
            failures.append(
                f"{path}: runs-on drift (expected {manifest.ci_runner}, found: {joined})"
            )

    setup_actions = re.findall(
        r"^\s*uses:\s*(actions/setup-python@[^\s]+)",
        text,
        flags=re.MULTILINE,
    )
    if not setup_actions:
        failures.append(f"{path}: actions/setup-python step is missing")
    else:
        bad_actions = sorted({item for item in setup_actions if item != manifest.python_setup_action})
        if bad_actions:
            joined = ", ".join(bad_actions)
            failures.append(
                f"{path}: setup-python action drift (expected {manifest.python_setup_action}, found: {joined})"
            )

    python_versions = re.findall(
        r"^\s*python-version:\s*['\"]?([^'\"\n]+)['\"]?\s*$",
        text,
        flags=re.MULTILINE,
    )
    if not python_versions:
        failures.append(f"{path}: python-version entries are missing")
    else:
        normalized = sorted({item.strip() for item in python_versions if str(item).strip()})
        bad_versions = sorted({item for item in normalized if item != manifest.python_version})
        if bad_versions:
            joined = ", ".join(bad_versions)
            failures.append(
                f"{path}: python-version drift (expected {manifest.python_version}, found: {joined})"
            )

    return failures


def _check_swift_tools_version(path: Path, manifest: ToolchainManifest) -> list[str]:
    if not path.exists():
        return [f"swift package file not found: {path}"]
    text = path.read_text(encoding="utf-8")
    first_line = text.splitlines()[0] if text else ""
    match = re.search(r"swift-tools-version:\s*([0-9]+(?:\.[0-9]+)?)", first_line)
    if match is None:
        return [f"{path}: swift-tools-version header is missing"]
    actual = str(match.group(1)).strip()
    if actual != manifest.swift_tools_version:
        return [
            f"{path}: swift-tools-version drift (expected {manifest.swift_tools_version}, found: {actual})"
        ]
    return []


def _check_bootstrap_script(path: Path, manifest: ToolchainManifest) -> list[str]:
    if not path.exists():
        return [f"bootstrap script not found: {path}"]
    text = path.read_text(encoding="utf-8")
    expected = manifest.python_bootstrap_binary
    if f'PYTHON_BIN="${{AMARYLLIS_BOOTSTRAP_PYTHON:-{expected}}}"' not in text:
        return [
            f"{path}: bootstrap default python drift (expected AMARYLLIS_BOOTSTRAP_PYTHON fallback to {expected})"
        ]
    return []


def _python_version_for_executable(executable: str) -> tuple[str | None, str | None]:
    try:
        proc = subprocess.run(
            [
                executable,
                "-c",
                "import platform; print(platform.python_version())",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, str(exc)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        return None, stderr or f"exit code {proc.returncode}"
    return (proc.stdout or "").strip(), None


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"repo root not found: {repo_root}", file=sys.stderr)
        return 2

    manifest_path = _resolve_path(repo_root, args.manifest)
    try:
        manifest = _load_manifest(manifest_path)
    except ToolchainManifestError as exc:
        print(f"[toolchain-drift] FAILED: {exc}", file=sys.stderr)
        return 1

    failures: list[str] = []

    for workflow_rel in manifest.ci_workflows:
        workflow_path = _resolve_path(repo_root, workflow_rel)
        failures.extend(_check_workflow(workflow_path, manifest))

    swift_path = _resolve_path(repo_root, manifest.swift_package_file)
    failures.extend(_check_swift_tools_version(swift_path, manifest))

    bootstrap_path = _resolve_path(repo_root, manifest.bootstrap_script)
    failures.extend(_check_bootstrap_script(bootstrap_path, manifest))

    requested_python = str(args.check_python_executable or "").strip()
    if requested_python:
        actual_version, error = _python_version_for_executable(requested_python)
        if error is not None:
            failures.append(f"python executable check failed for '{requested_python}': {error}")
        elif actual_version != manifest.python_version:
            failures.append(
                "python executable version drift "
                f"(expected {manifest.python_version}, found {actual_version})"
            )

    if failures:
        print("[toolchain-drift] FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    checked = len(manifest.ci_workflows)
    print(
        "[toolchain-drift] OK "
        f"manifest={manifest.manifest_version} schema={manifest.schema_version} "
        f"workflows={checked}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
