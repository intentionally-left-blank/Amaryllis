#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Agent Factory baseline-refresh pull request description metadata "
            "(refresh artifact reference + approver identity)."
        )
    )
    parser.add_argument(
        "--event-path",
        default="",
        help="Path to GitHub event payload JSON (defaults to GITHUB_EVENT_PATH).",
    )
    parser.add_argument(
        "--output",
        default="artifacts/agent-factory-plan-perf-baseline-pr-description-gate-report.json",
        help="Output report path.",
    )
    parser.add_argument(
        "--allow-non-pr-events",
        action="store_true",
        help="Pass with status=skip when event payload is not a pull_request event.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"missing_event_payload:{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("event_payload_must_be_object")
    return payload


def _extract_line_value(body: str, patterns: list[re.Pattern[str]]) -> str:
    for pattern in patterns:
        match = pattern.search(body)
        if match is None:
            continue
        value = str(match.group("value") or "").strip()
        if value:
            return value
    return ""


def _normalize_token(value: str) -> str:
    return str(value or "").strip().lower()


def _is_placeholder(value: str) -> bool:
    normalized = _normalize_token(value)
    if not normalized:
        return True
    if normalized in {
        "todo",
        "tbd",
        "n/a",
        "na",
        "none",
        "pending",
        "<todo>",
        "<tbd>",
        "<pending>",
        "<@github-handle>",
        "<@maintainer>",
        "@approver-github-handle",
        "@maintainer",
    }:
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return False


def _looks_like_artifact_reference(value: str) -> bool:
    candidate = str(value or "").strip()
    if _is_placeholder(candidate):
        return False
    lowered = candidate.lower()
    markers = (
        "https://",
        "http://",
        "actions/runs/",
        "artifact",
        ".json",
        "agent-factory-plan-perf-baseline-refresh",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_approver_identity(value: str) -> bool:
    candidate = str(value or "").strip()
    if _is_placeholder(candidate):
        return False
    if re.search(r"@[a-z0-9][a-z0-9-]{0,38}", candidate, flags=re.IGNORECASE):
        return True
    if re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", candidate, flags=re.IGNORECASE):
        return True
    return False


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    output_path = _resolve_path(project_root, str(args.output))

    event_path_raw = str(args.event_path or "").strip() or str(os.getenv("GITHUB_EVENT_PATH") or "").strip()
    if not event_path_raw:
        print("[agent-factory-plan-perf-baseline-pr-description-gate] input_error=missing_event_path", file=sys.stderr)
        return 2
    event_path = _resolve_path(project_root, event_path_raw)

    try:
        event_payload = _load_json_object(event_path)
    except Exception as exc:
        print(f"[agent-factory-plan-perf-baseline-pr-description-gate] input_error={exc}", file=sys.stderr)
        return 2

    pull_request = event_payload.get("pull_request") if isinstance(event_payload.get("pull_request"), dict) else None
    if pull_request is None:
        report = {
            "generated_at": _utc_now_iso(),
            "suite": "agent_factory_plan_perf_baseline_pr_description_gate_v1",
            "summary": {
                "status": "skip" if args.allow_non_pr_events else "fail",
                "checks_total": 0,
                "checks_failed": 0 if args.allow_non_pr_events else 1,
                "reason": "non_pull_request_event",
            },
            "checks": [],
            "event_path": str(event_path),
        }
        _write_json(output_path, report)
        if args.allow_non_pr_events:
            print(
                "[agent-factory-plan-perf-baseline-pr-description-gate] OK "
                f"status=skip reason=non_pull_request_event report={output_path}"
            )
            return 0
        print(
            "[agent-factory-plan-perf-baseline-pr-description-gate] FAILED "
            f"reason=non_pull_request_event report={output_path}"
        )
        return 1

    body = str(pull_request.get("body") or "")
    artifact_value = _extract_line_value(
        body,
        patterns=[
            re.compile(r"(?:^|\n)\s*-?\s*refresh artifact reference\s*:\s*(?P<value>[^\n]+)", re.IGNORECASE),
            re.compile(r"(?:^|\n)\s*-?\s*refresh artifact\s*:\s*(?P<value>[^\n]+)", re.IGNORECASE),
            re.compile(r"(?:^|\n)\s*-?\s*baseline refresh artifact\s*:\s*(?P<value>[^\n]+)", re.IGNORECASE),
        ],
    )
    approver_value = _extract_line_value(
        body,
        patterns=[
            re.compile(r"(?:^|\n)\s*-?\s*approver identity\s*:\s*(?P<value>[^\n]+)", re.IGNORECASE),
            re.compile(r"(?:^|\n)\s*-?\s*approved by\s*:\s*(?P<value>[^\n]+)", re.IGNORECASE),
            re.compile(r"(?:^|\n)\s*-?\s*approver\s*:\s*(?P<value>[^\n]+)", re.IGNORECASE),
        ],
    )

    checks = [
        {
            "name": "pr_body.refresh_artifact_reference",
            "passed": _looks_like_artifact_reference(artifact_value),
            "detail": artifact_value if artifact_value else "missing",
        },
        {
            "name": "pr_body.approver_identity",
            "passed": _looks_like_approver_identity(approver_value),
            "detail": approver_value if approver_value else "missing",
        },
    ]
    failed_checks = [item for item in checks if not bool(item.get("passed"))]
    status = "pass" if not failed_checks else "fail"

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "agent_factory_plan_perf_baseline_pr_description_gate_v1",
        "summary": {
            "status": status,
            "checks_total": len(checks),
            "checks_failed": len(failed_checks),
        },
        "pull_request": {
            "number": int(pull_request.get("number") or event_payload.get("number") or 0),
            "title": str(pull_request.get("title") or ""),
        },
        "values": {
            "refresh_artifact_reference": artifact_value,
            "approver_identity": approver_value,
        },
        "checks": checks,
        "event_path": str(event_path),
    }
    _write_json(output_path, report)

    if failed_checks:
        print(
            "[agent-factory-plan-perf-baseline-pr-description-gate] FAILED "
            f"checks_failed={len(failed_checks)} report={output_path}"
        )
        return 1

    print(
        "[agent-factory-plan-perf-baseline-pr-description-gate] OK "
        f"checks={len(checks)} report={output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
