#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

EXPECTED_REFRESH_SUITE = "agent_factory_plan_perf_baseline_refresh_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate an Agent Factory baseline-refresh pull request template from "
            "baseline refresh artifacts with prefilled change_control metadata."
        )
    )
    parser.add_argument(
        "--refresh-report",
        default="artifacts/agent-factory-plan-perf-baseline-refresh-report.json",
        help="Path to baseline refresh report JSON.",
    )
    parser.add_argument(
        "--suggested-baseline",
        default="artifacts/agent_factory_plan_perf_envelope_suggested.json",
        help="Path to suggested baseline JSON artifact (for traceability in template).",
    )
    parser.add_argument(
        "--output",
        default="artifacts/agent-factory-plan-perf-baseline-pr-template.md",
        help="Output markdown template path.",
    )
    parser.add_argument(
        "--metadata-output",
        default="",
        help="Optional output JSON with the prefilled change_control payload.",
    )
    parser.add_argument(
        "--ticket",
        default="P8-A10/P8-A13",
        help="Default ticket identifier for generated change_control.",
    )
    parser.add_argument(
        "--requested-by",
        default="amaryllis-baseline-refresh-bot",
        help="Default requested_by value for generated change_control.",
    )
    parser.add_argument(
        "--change-id-prefix",
        default="agent-factory-perf-envelope-refresh",
        help="Prefix for generated change_control.change_id.",
    )
    parser.add_argument(
        "--approver-placeholder",
        default="@approver-github-handle",
        help="Placeholder used in approved_by in generated change_control payload.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json_object(path: Path, *, error_prefix: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{error_prefix}: missing file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{error_prefix}: JSON root must be object: {path}")
    return payload


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _normalized_change_id(*, prefix: str, generated_at: str) -> str:
    date_compact = re.sub(r"[^0-9]", "", generated_at)[:8]
    if not date_compact:
        date_compact = datetime.now(timezone.utc).strftime("%Y%m%d")
    normalized_prefix = re.sub(r"[^a-z0-9-]+", "-", prefix.strip().lower()).strip("-") or "baseline-refresh"
    return f"{normalized_prefix}-{date_compact}"


def _extract_profiles(refresh_report: dict[str, Any]) -> list[dict[str, Any]]:
    raw_profiles = refresh_report.get("profiles")
    if not isinstance(raw_profiles, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_profiles:
        if not isinstance(item, dict):
            continue
        profile = str(item.get("profile") or "").strip()
        if not profile:
            continue
        observed = item.get("observed") if isinstance(item.get("observed"), dict) else {}
        baseline_thresholds = item.get("baseline_thresholds") if isinstance(item.get("baseline_thresholds"), dict) else {}
        suggested_thresholds = item.get("suggested_thresholds") if isinstance(item.get("suggested_thresholds"), dict) else {}
        drift = item.get("drift") if isinstance(item.get("drift"), dict) else {}
        rows.append(
            {
                "profile": profile,
                "status": str(item.get("status") or "unknown").strip().lower(),
                "observed_p95_ms": round(_safe_float(observed.get("p95_latency_ms")), 4),
                "baseline_p95_ms": round(_safe_float(baseline_thresholds.get("max_p95_latency_ms")), 4),
                "suggested_p95_ms": round(_safe_float(suggested_thresholds.get("max_p95_latency_ms")), 4),
                "delta_pct": round(_safe_float(drift.get("p95_threshold_delta_pct")), 4),
            }
        )
    return rows


def _render_markdown(
    *,
    refresh_report_path: Path,
    suggested_baseline_path: Path,
    refresh_report: dict[str, Any],
    generated_change_control: dict[str, Any],
) -> str:
    summary = refresh_report.get("summary") if isinstance(refresh_report.get("summary"), dict) else {}
    profiles = _extract_profiles(refresh_report)
    status = str(summary.get("status") or "unknown").strip().lower() or "unknown"
    profiles_warn = int(summary.get("profiles_warn") or 0)
    profiles_fail = int(summary.get("profiles_fail") or 0)

    lines: list[str] = [
        "## Agent Factory Baseline Refresh",
        "",
        f"- Refresh artifact reference: `<paste GitHub Actions artifact URL for {refresh_report_path.name}>`",
        f"- Refresh report path: `{refresh_report_path}`",
        f"- Suggested baseline path: `{suggested_baseline_path}`",
        "- Approver identity: `<@github-handle>`",
        "- Approval timestamp (UTC ISO8601): `<YYYY-MM-DDTHH:MM:SSZ>`",
        "",
        "## Refresh Snapshot",
        "",
        f"- status: `{status}`",
        f"- profiles_warn: `{profiles_warn}`",
        f"- profiles_fail: `{profiles_fail}`",
    ]

    if profiles:
        lines.extend(
            [
                "",
                "## Profile Drift Summary",
                "",
                "| profile | status | observed p95 (ms) | baseline p95 (ms) | suggested p95 (ms) | delta % |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in profiles:
            lines.append(
                "| {profile} | {status} | {observed:.4f} | {baseline:.4f} | {suggested:.4f} | {delta:.4f} |".format(
                    profile=item["profile"],
                    status=item["status"],
                    observed=float(item["observed_p95_ms"]),
                    baseline=float(item["baseline_p95_ms"]),
                    suggested=float(item["suggested_p95_ms"]),
                    delta=float(item["delta_pct"]),
                )
            )

    lines.extend(
        [
            "",
            "## Prefilled change_control",
            "",
            "Copy this block into `eval/baselines/quality/agent_factory_plan_perf_envelope.json` before merge:",
            "",
            "```json",
            json.dumps(generated_change_control, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Validation Checklist",
            "",
            "- [ ] Baseline file includes the `change_control` block shown above.",
            "- [ ] PR description keeps `Refresh artifact reference` and `Approver identity` filled with real values.",
            "- [ ] Manual approval metadata (`approved_by`, `approved_at`) is updated from placeholders.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]

    refresh_report_path = _resolve_path(project_root, str(args.refresh_report))
    suggested_baseline_path = _resolve_path(project_root, str(args.suggested_baseline))
    output_path = _resolve_path(project_root, str(args.output))
    metadata_output_raw = str(args.metadata_output or "").strip()
    metadata_output_path = _resolve_path(project_root, metadata_output_raw) if metadata_output_raw else None

    try:
        refresh_report = _load_json_object(refresh_report_path, error_prefix="refresh_report")
    except Exception as exc:
        print(f"[agent-factory-plan-perf-baseline-pr-template] input_error={exc}", file=sys.stderr)
        return 2

    suite = str(refresh_report.get("suite") or "").strip()
    if suite != EXPECTED_REFRESH_SUITE:
        print(
            "[agent-factory-plan-perf-baseline-pr-template] "
            f"input_error=unexpected_refresh_suite:{suite or '<empty>'}",
            file=sys.stderr,
        )
        return 2

    refresh_generated_at = str(refresh_report.get("generated_at") or "").strip()
    generated_at = _utc_now_iso()
    profiles = _extract_profiles(refresh_report)
    approval_scope = [str(item.get("profile") or "").strip() for item in profiles if str(item.get("profile") or "").strip()]
    if not approval_scope:
        approval_scope = ["release", "nightly", "dev_macos", "dev_linux"]

    summary = refresh_report.get("summary") if isinstance(refresh_report.get("summary"), dict) else {}
    summary_status = str(summary.get("status") or "unknown").strip().lower() or "unknown"
    generated_change_control = {
        "change_id": _normalized_change_id(prefix=str(args.change_id_prefix), generated_at=refresh_generated_at or generated_at),
        "reason": (
            "Refresh Agent Factory plan perf baseline envelope from automated drift report "
            f"(status={summary_status}, profiles={len(profiles)})."
        ),
        "ticket": str(args.ticket or "").strip() or "P8-A10/P8-A13",
        "requested_by": str(args.requested_by or "").strip() or "amaryllis-baseline-refresh-bot",
        "manual_approval": True,
        "approved_by": [str(args.approver_placeholder or "@approver-github-handle").strip() or "@approver-github-handle"],
        "approved_at": "<YYYY-MM-DDTHH:MM:SSZ>",
        "approval_scope": approval_scope,
    }

    markdown = _render_markdown(
        refresh_report_path=refresh_report_path,
        suggested_baseline_path=suggested_baseline_path,
        refresh_report=refresh_report,
        generated_change_control=generated_change_control,
    )
    _write_text(output_path, markdown)

    if metadata_output_path is not None:
        metadata_payload = {
            "generated_at": generated_at,
            "suite": "agent_factory_plan_perf_baseline_pr_template_v1",
            "inputs": {
                "refresh_report": str(refresh_report_path),
                "suggested_baseline": str(suggested_baseline_path),
            },
            "summary": {
                "status": summary_status,
                "profiles_total": len(profiles),
            },
            "change_control": generated_change_control,
        }
        _write_json(metadata_output_path, metadata_payload)

    print(
        "[agent-factory-plan-perf-baseline-pr-template] OK "
        f"output={output_path} profiles={len(profiles)}"
    )
    if metadata_output_path is not None:
        print(f"[agent-factory-plan-perf-baseline-pr-template] metadata={metadata_output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
