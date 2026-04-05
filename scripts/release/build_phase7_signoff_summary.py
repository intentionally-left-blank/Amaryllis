#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a compact Phase 7 sign-off summary from release-cut gate artifacts."
        )
    )
    parser.add_argument(
        "--phase7-gate-report",
        default="artifacts/phase7-release-cut-gate-report.json",
        help="Path to phase7_release_cut_gate report.",
    )
    parser.add_argument(
        "--news-report",
        default="artifacts/news-mission/news-mission-gate-report.json",
        help="Path to news mission gate report.",
    )
    parser.add_argument(
        "--provider-report",
        default="artifacts/provider-session-policy-check-report.json",
        help="Path to provider session policy check report.",
    )
    parser.add_argument(
        "--mission-report",
        default="artifacts/mission-success-recovery-report.json",
        help="Path to mission success/recovery report pack.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/phase7-signoff-summary.json",
        help="Output summary JSON path.",
    )
    parser.add_argument(
        "--markdown-output",
        default="",
        help="Optional markdown summary output path.",
    )
    parser.add_argument(
        "--allow-failed-status",
        action="store_true",
        help="Do not fail when phase7 gate summary.status is fail.",
    )
    return parser.parse_args()


def _resolve_path(project_root: Path, raw: str) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = project_root / path
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _extract_status(summary_payload: dict[str, Any]) -> str:
    summary = summary_payload.get("summary") if isinstance(summary_payload.get("summary"), dict) else {}
    return str(summary.get("status") or "unknown").strip().lower() or "unknown"


def _render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    kpis = payload.get("kpis") if isinstance(payload.get("kpis"), dict) else {}
    lines = [
        "# Phase 7 Sign-Off Summary",
        "",
        f"- Generated at: `{payload.get('generated_at')}`",
        f"- Overall status: `{summary.get('status')}`",
        f"- Checks: `{summary.get('checks_passed')}/{summary.get('checks_total')}`",
        "",
        "## KPI Snapshot",
        "",
        f"- `news_citation_coverage_rate`: `{kpis.get('news_citation_coverage_rate')}`",
        f"- `news_mission_success_rate_pct`: `{kpis.get('news_mission_success_rate_pct')}`",
        f"- `mission_success_rate_pct`: `{kpis.get('mission_success_rate_pct')}`",
        "",
        "## Artifact Paths",
        "",
    ]
    for key in ("phase7_gate_report", "news_report", "provider_report", "mission_report"):
        lines.append(f"- `{key}`: `{artifacts.get(key)}`")
    if checks:
        lines.extend(["", "## Failed Checks", ""])
        failed = [item for item in checks if isinstance(item, dict) and not bool(item.get("ok"))]
        if not failed:
            lines.append("- none")
        else:
            for item in failed:
                lines.append(f"- `{item.get('name')}`: {item.get('detail')}")
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    phase7_gate_report_path = _resolve_path(project_root, str(args.phase7_gate_report))
    news_report_path = _resolve_path(project_root, str(args.news_report))
    provider_report_path = _resolve_path(project_root, str(args.provider_report))
    mission_report_path = _resolve_path(project_root, str(args.mission_report))

    try:
        phase7_payload = _load_json_object(phase7_gate_report_path)
        news_payload = _load_json_object(news_report_path)
        provider_payload = _load_json_object(provider_report_path)
        mission_payload = _load_json_object(mission_report_path)
    except Exception as exc:
        print(f"[phase7-signoff-summary] FAILED load_error={type(exc).__name__}: {exc}")
        return 2

    phase7_summary = phase7_payload.get("summary") if isinstance(phase7_payload.get("summary"), dict) else {}
    phase7_status = str(phase7_summary.get("status") or "").strip().lower() or "unknown"
    phase7_checks_total = int(phase7_summary.get("checks_total") or 0)
    phase7_checks_failed = int(phase7_summary.get("checks_failed") or 0)

    mission_kpis = mission_payload.get("kpis") if isinstance(mission_payload.get("kpis"), dict) else {}
    output_payload = {
        "generated_at": _utc_now_iso(),
        "suite": "phase7_signoff_summary_v1",
        "summary": {
            "status": phase7_status,
            "checks_total": phase7_checks_total,
            "checks_failed": phase7_checks_failed,
            "checks_passed": max(0, phase7_checks_total - phase7_checks_failed),
            "components": {
                "phase7_gate": _extract_status(phase7_payload),
                "news_gate": _extract_status(news_payload),
                "provider_policy_gate": _extract_status(provider_payload),
                "mission_pack": _extract_status(mission_payload),
            },
        },
        "kpis": {
            "news_citation_coverage_rate": round(_safe_float(mission_kpis.get("news_citation_coverage_rate")), 6),
            "news_mission_success_rate_pct": round(_safe_float(mission_kpis.get("news_mission_success_rate_pct")), 4),
            "mission_success_rate_pct": round(_safe_float(mission_kpis.get("mission_success_rate_pct")), 4),
        },
        "artifacts": {
            "phase7_gate_report": str(phase7_gate_report_path),
            "news_report": str(news_report_path),
            "provider_report": str(provider_report_path),
            "mission_report": str(mission_report_path),
        },
        "checks": list(phase7_payload.get("checks") or []),
    }

    output_path = _resolve_path(project_root, str(args.output))
    _write_json(output_path, output_payload)
    print(f"[phase7-signoff-summary] report={output_path}")

    markdown_raw = str(args.markdown_output or "").strip()
    if markdown_raw:
        markdown_path = _resolve_path(project_root, markdown_raw)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(_render_markdown(output_payload), encoding="utf-8")
        print(f"[phase7-signoff-summary] markdown={markdown_path}")

    if phase7_status != "pass" and not bool(args.allow_failed_status):
        print("[phase7-signoff-summary] FAILED phase7_gate_status_not_pass")
        return 1
    print("[phase7-signoff-summary] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
