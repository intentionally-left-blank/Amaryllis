#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate RU/EN localization baseline and OSS governance package "
            "(DCO/CoC/maintainers/trademark/contributor docs)."
        )
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root path (default: current repo root).",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _check_file(
    *,
    root: Path,
    relpath: str,
    required_snippets: list[str],
    require_cyrillic: bool = False,
) -> dict[str, Any]:
    path = root / relpath
    if not path.exists():
        return {
            "id": relpath,
            "ok": False,
            "error": "file_missing",
            "path": str(path),
            "missing_snippets": required_snippets,
            "missing_cyrillic": require_cyrillic,
        }

    text = path.read_text(encoding="utf-8")
    missing_snippets = [item for item in required_snippets if item not in text]
    missing_cyrillic = bool(require_cyrillic and _CYRILLIC_RE.search(text) is None)
    ok = not missing_snippets and not missing_cyrillic
    return {
        "id": relpath,
        "ok": ok,
        "error": "" if ok else "contract_mismatch",
        "path": str(path),
        "missing_snippets": missing_snippets,
        "missing_cyrillic": missing_cyrillic,
    }


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    root = _resolve_path(repo_root, str(args.root))

    checks: list[dict[str, Any]] = []
    checks.append(
        _check_file(
            root=root,
            relpath="CONTRIBUTING.md",
            required_snippets=[
                "DCO",
                "Signed-off-by:",
                "How To Contribute",
            ],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="CODE_OF_CONDUCT.md",
            required_snippets=[
                "Code of Conduct",
                "inclusive",
                "report",
            ],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="GOVERNANCE.md",
            required_snippets=[
                "Governance",
                "Maintainer",
                "decision",
                "release",
            ],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="MAINTAINERS.md",
            required_snippets=[
                "Maintainers",
                "| Area |",
                "| Primary |",
            ],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="TRADEMARK_POLICY.md",
            required_snippets=[
                "Trademark",
                "Amaryllis",
                "permission",
            ],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="DCO.md",
            required_snippets=[
                "Developer Certificate of Origin",
                "Signed-off-by:",
            ],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath=".github/PULL_REQUEST_TEMPLATE.md",
            required_snippets=[
                "Signed-off-by:",
                "DCO",
                "Checklist",
            ],
        )
    )

    checks.append(
        _check_file(
            root=root,
            relpath="docs/localization/ru/quickstart.md",
            required_snippets=["# Быстрый старт", "API", "Проверка"],
            require_cyrillic=True,
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="docs/localization/ru/starter-prompts.md",
            required_snippets=["# Стартовые промпты", "### Шаблон 1", "### Шаблон 2"],
            require_cyrillic=True,
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="docs/localization/ru/starter-workflows.md",
            required_snippets=["# Стартовые workflow", "### Сценарий 1", "### Сценарий 2"],
            require_cyrillic=True,
        )
    )

    checks.append(
        _check_file(
            root=root,
            relpath="docs/localization/en/quickstart.md",
            required_snippets=["# Quickstart", "API", "Verification"],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="docs/localization/en/starter-prompts.md",
            required_snippets=["# Starter Prompts", "### Template 1", "### Template 2"],
        )
    )
    checks.append(
        _check_file(
            root=root,
            relpath="docs/localization/en/starter-workflows.md",
            required_snippets=["# Starter Workflows", "### Scenario 1", "### Scenario 2"],
        )
    )

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "localization_governance_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "root": str(root),
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if failed:
        print("[localization-governance-gate] FAILED")
        for item in failed:
            print(f"- {item.get('id')}: {item.get('error')}")
            missing_snippets = item.get("missing_snippets", [])
            if missing_snippets:
                print(f"  missing snippets: {', '.join(str(x) for x in missing_snippets)}")
            if bool(item.get("missing_cyrillic")):
                print("  missing cyrillic content")
        return 1

    print(f"[localization-governance-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
