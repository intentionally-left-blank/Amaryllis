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
            "Validate Phase 7 release cut DoD using news/provider/report-pack artifacts "
            "and KPI thresholds."
        )
    )
    parser.add_argument(
        "--news-report",
        default="artifacts/news-mission-gate-report.json",
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
        "--mission-pack-gate-report",
        default="artifacts/mission-report-pack-gate-report.json",
        help="Path to mission report pack gate report.",
    )
    parser.add_argument(
        "--min-news-citation-coverage",
        type=float,
        default=0.95,
        help="Minimum allowed news_citation_coverage_rate.",
    )
    parser.add_argument(
        "--min-news-mission-success-rate-pct",
        type=float,
        default=99.0,
        help="Minimum allowed news_mission_success_rate_pct.",
    )
    parser.add_argument(
        "--min-mission-success-rate-pct",
        type=float,
        default=99.0,
        help="Minimum allowed mission_success_rate_pct.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output report path.",
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


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]

    news_report_path = _resolve_path(project_root, str(args.news_report))
    provider_report_path = _resolve_path(project_root, str(args.provider_report))
    mission_report_path = _resolve_path(project_root, str(args.mission_report))
    mission_pack_gate_report_path = _resolve_path(project_root, str(args.mission_pack_gate_report))

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def load_or_fail(name: str, path: Path) -> dict[str, Any]:
        if not path.exists():
            add_check(f"{name}.exists", False, f"missing: {path}")
            return {}
        add_check(f"{name}.exists", True, str(path))
        try:
            payload = _load_json_object(path)
        except Exception as exc:
            add_check(f"{name}.json_valid", False, f"{type(exc).__name__}: {exc}")
            return {}
        add_check(f"{name}.json_valid", True, "ok")
        return payload

    news_payload = load_or_fail("news_report", news_report_path)
    provider_payload = load_or_fail("provider_report", provider_report_path)
    mission_payload = load_or_fail("mission_report", mission_report_path)
    mission_pack_payload = load_or_fail("mission_pack_gate_report", mission_pack_gate_report_path)

    news_suite = str(news_payload.get("suite") or "").strip()
    add_check("news_report.suite", news_suite == "news_mission_gate_v1", f"suite={news_suite}")
    news_summary = news_payload.get("summary") if isinstance(news_payload.get("summary"), dict) else {}
    news_status = str(news_summary.get("status") or "").strip().lower()
    news_failed = int(news_summary.get("checks_failed") or 0)
    add_check("news_report.summary_status", news_status == "pass", f"status={news_status}")
    add_check("news_report.checks_failed_zero", news_failed == 0, f"checks_failed={news_failed}")

    provider_suite = str(provider_payload.get("suite") or "").strip()
    add_check(
        "provider_report.suite",
        provider_suite == "provider_session_policy_check_v1",
        f"suite={provider_suite}",
    )
    provider_summary = (
        provider_payload.get("summary") if isinstance(provider_payload.get("summary"), dict) else {}
    )
    provider_status = str(provider_summary.get("status") or "").strip().lower()
    provider_failed = int(provider_summary.get("checks_failed") or 0)
    add_check("provider_report.summary_status", provider_status == "pass", f"status={provider_status}")
    add_check(
        "provider_report.checks_failed_zero",
        provider_failed == 0,
        f"checks_failed={provider_failed}",
    )

    mission_suite = str(mission_payload.get("suite") or "").strip()
    add_check(
        "mission_report.suite",
        mission_suite == "mission_success_recovery_report_pack_v2",
        f"suite={mission_suite}",
    )
    mission_summary = mission_payload.get("summary") if isinstance(mission_payload.get("summary"), dict) else {}
    mission_status = str(mission_summary.get("status") or "").strip().lower()
    mission_failed = int(mission_summary.get("checks_failed") or 0)
    add_check("mission_report.summary_status", mission_status == "pass", f"status={mission_status}")
    add_check("mission_report.checks_failed_zero", mission_failed == 0, f"checks_failed={mission_failed}")

    mission_pack_suite = str(mission_pack_payload.get("suite") or "").strip()
    add_check(
        "mission_pack_gate_report.suite",
        mission_pack_suite == "mission_report_pack_gate_v1",
        f"suite={mission_pack_suite}",
    )
    mission_pack_summary = (
        mission_pack_payload.get("summary") if isinstance(mission_pack_payload.get("summary"), dict) else {}
    )
    mission_pack_status = str(mission_pack_summary.get("status") or "").strip().lower()
    add_check(
        "mission_pack_gate_report.summary_status",
        mission_pack_status == "pass",
        f"status={mission_pack_status}",
    )

    kpis = mission_payload.get("kpis") if isinstance(mission_payload.get("kpis"), dict) else {}
    news_citation_coverage_rate = _safe_float(kpis.get("news_citation_coverage_rate"))
    news_mission_success_rate_pct = _safe_float(kpis.get("news_mission_success_rate_pct"))
    mission_success_rate_pct = _safe_float(kpis.get("mission_success_rate_pct"))
    add_check(
        "kpi.news_citation_coverage_rate",
        news_citation_coverage_rate >= float(args.min_news_citation_coverage),
        (
            f"value={news_citation_coverage_rate:.6f} "
            f"min={float(args.min_news_citation_coverage):.6f}"
        ),
    )
    add_check(
        "kpi.news_mission_success_rate_pct",
        news_mission_success_rate_pct >= float(args.min_news_mission_success_rate_pct),
        (
            f"value={news_mission_success_rate_pct:.4f} "
            f"min={float(args.min_news_mission_success_rate_pct):.4f}"
        ),
    )
    add_check(
        "kpi.mission_success_rate_pct",
        mission_success_rate_pct >= float(args.min_mission_success_rate_pct),
        f"value={mission_success_rate_pct:.4f} min={float(args.min_mission_success_rate_pct):.4f}",
    )

    failed = [item for item in checks if not bool(item.get("ok"))]
    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "phase7_release_cut_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "thresholds": {
                "min_news_citation_coverage": float(args.min_news_citation_coverage),
                "min_news_mission_success_rate_pct": float(args.min_news_mission_success_rate_pct),
                "min_mission_success_rate_pct": float(args.min_mission_success_rate_pct),
            },
            "kpis": {
                "news_citation_coverage_rate": round(news_citation_coverage_rate, 6),
                "news_mission_success_rate_pct": round(news_mission_success_rate_pct, 4),
                "mission_success_rate_pct": round(mission_success_rate_pct, 4),
            },
        },
        "artifacts": {
            "news_report": str(news_report_path),
            "provider_report": str(provider_report_path),
            "mission_report": str(mission_report_path),
            "mission_pack_gate_report": str(mission_pack_gate_report_path),
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(project_root, output_raw)
        _write_json(output_path, payload)
        print(f"[phase7-release-cut-gate] report={output_path}")

    if failed:
        print("[phase7-release-cut-gate] FAILED")
        for item in failed:
            print(f"- {item.get('name')}: {item.get('detail')}")
        return 1
    print(f"[phase7-release-cut-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
