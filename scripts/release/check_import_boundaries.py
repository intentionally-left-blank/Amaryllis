#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import sys


LAYER_PACKAGES: dict[str, tuple[str, ...]] = {
    "api": ("api",),
    "orchestration": ("agents", "automation", "controller", "planner", "tasks"),
    "kernel": ("kernel",),
    "storage": ("storage",),
    "ui": ("macos",),
}

FORBIDDEN_LAYER_IMPORTS: set[tuple[str, str]] = {
    ("api", "storage"),
    ("api", "orchestration"),
    ("orchestration", "api"),
    ("storage", "api"),
    ("storage", "orchestration"),
    ("storage", "kernel"),
    ("kernel", "api"),
    ("kernel", "orchestration"),
    ("kernel", "storage"),
    ("kernel", "ui"),
    ("ui", "api"),
    ("ui", "orchestration"),
    ("ui", "kernel"),
    ("ui", "storage"),
}

CHECKED_LAYER_DIRS: tuple[str, ...] = (
    "api",
    "agents",
    "automation",
    "controller",
    "planner",
    "tasks",
    "kernel",
    "storage",
    "macos",
)


@dataclass(frozen=True)
class ImportOccurrence:
    imported_module: str
    line: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check architectural import boundaries between core layers.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root path to scan",
    )
    return parser.parse_args()


def _package_to_layer(package: str) -> str | None:
    normalized = str(package or "").strip()
    if not normalized:
        return None
    for layer, packages in LAYER_PACKAGES.items():
        if normalized in packages:
            return layer
    return None


def _source_layer_for_file(repo_root: Path, file_path: Path) -> str | None:
    try:
        relative = file_path.relative_to(repo_root)
    except ValueError:
        return None
    if not relative.parts:
        return None
    top_level = relative.parts[0]
    return _package_to_layer(top_level)


def _module_parts_for_file(repo_root: Path, file_path: Path) -> list[str]:
    relative = file_path.relative_to(repo_root)
    parts = list(relative.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return [item for item in parts if item]


def _resolve_from_import_module(
    *,
    module: str | None,
    level: int,
    source_module_parts: list[str],
) -> str:
    if level <= 0:
        return str(module or "").strip()

    module_base = source_module_parts[:-1]
    parent = module_base[: max(0, len(module_base) - level + 1)]
    module_tail = str(module or "").strip()
    if module_tail:
        return ".".join([*parent, module_tail])
    return ".".join(parent)


def _iter_imports(file_path: Path, *, repo_root: Path) -> list[ImportOccurrence]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = file_path.read_text(encoding="latin-1")
    tree = ast.parse(source, filename=str(file_path))
    source_module_parts = _module_parts_for_file(repo_root=repo_root, file_path=file_path)
    occurrences: list[ImportOccurrence] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = str(alias.name or "").strip()
                if module:
                    occurrences.append(
                        ImportOccurrence(
                            imported_module=module,
                            line=int(getattr(node, "lineno", 1)),
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_from_import_module(
                module=node.module,
                level=int(getattr(node, "level", 0)),
                source_module_parts=source_module_parts,
            )
            if resolved:
                occurrences.append(
                    ImportOccurrence(
                        imported_module=resolved,
                        line=int(getattr(node, "lineno", 1)),
                    )
                )
    return occurrences


def _iter_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for root_name in CHECKED_LAYER_DIRS:
        root = repo_root / root_name
        if not root.exists():
            continue
        for item in root.rglob("*.py"):
            if "__pycache__" in item.parts:
                continue
            files.append(item)
    return sorted(files)


def main() -> int:
    args = _parse_args()
    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"repo root not found: {repo_root}", file=sys.stderr)
        return 2

    violations: list[str] = []
    scanned_files = 0
    for file_path in _iter_python_files(repo_root):
        source_layer = _source_layer_for_file(repo_root=repo_root, file_path=file_path)
        if source_layer is None:
            continue
        scanned_files += 1

        for occurrence in _iter_imports(file_path=file_path, repo_root=repo_root):
            imported_top_level = occurrence.imported_module.split(".", 1)[0].strip()
            target_layer = _package_to_layer(imported_top_level)
            if target_layer is None:
                continue
            if (source_layer, target_layer) not in FORBIDDEN_LAYER_IMPORTS:
                continue
            relative = file_path.relative_to(repo_root)
            violations.append(
                f"{relative}:{occurrence.line} forbidden import {source_layer}->{target_layer}: {occurrence.imported_module}"
            )

    if violations:
        print("import boundary check failed:", file=sys.stderr)
        for item in violations:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"import boundary check OK: scanned {scanned_files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
