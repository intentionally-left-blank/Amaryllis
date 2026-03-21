#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish mission success/recovery report into a stable runtime path "
            "for nightly/release observability export."
        )
    )
    parser.add_argument(
        "--report",
        default="artifacts/nightly-mission-success-recovery-report.json",
        help="Path to mission success/recovery report JSON.",
    )
    parser.add_argument(
        "--channel",
        default="nightly",
        choices=("nightly", "release"),
        help="Output channel used for default output filename.",
    )
    parser.add_argument(
        "--expect-scope",
        default="auto",
        choices=("auto", "nightly", "release"),
        help="Optional strict scope check against report payload.",
    )
    parser.add_argument(
        "--install-root",
        default=str(Path.home() / ".local" / "share" / "amaryllis"),
        help="Install root for default publish location.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional explicit output path for published report.",
    )
    return parser.parse_args()


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(str(raw_path).strip()).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be object: {path}")
    return payload


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]

    report_path = _resolve_path(project_root, str(args.report))
    if not report_path.exists():
        print(f"[mission-report-publish] missing report: {report_path}", file=sys.stderr)
        return 2

    try:
        report = _load_json_object(report_path)
    except Exception as exc:
        print(f"[mission-report-publish] invalid report: {report_path} error={exc}", file=sys.stderr)
        return 2

    suite = str(report.get("suite") or "").strip()
    if suite != "mission_success_recovery_report_pack_v2":
        print(
            (
                "[mission-report-publish] unexpected suite: "
                f"{suite!r} (expected 'mission_success_recovery_report_pack_v2')"
            ),
            file=sys.stderr,
        )
        return 2

    expected_scope = str(args.expect_scope or "auto").strip().lower()
    if expected_scope != "auto":
        scope = str(report.get("scope") or "").strip().lower()
        if scope != expected_scope:
            print(
                (
                    "[mission-report-publish] scope mismatch: "
                    f"report={scope!r} expected={expected_scope!r}"
                ),
                file=sys.stderr,
            )
            return 2

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(project_root, output_raw)
    else:
        install_root = Path(str(args.install_root)).expanduser()
        if str(args.channel) == "release":
            output_path = install_root / "observability" / "release-mission-success-recovery-latest.json"
        else:
            output_path = install_root / "observability" / "nightly-mission-success-recovery-latest.json"

    _write_json_atomically(output_path, report)
    print(f"[mission-report-publish] report={output_path}")
    print("[mission-report-publish] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
