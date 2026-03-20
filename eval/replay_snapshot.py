from __future__ import annotations

from typing import Any


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_stage_counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        stage = _as_text(key).lower()
        if not stage:
            continue
        normalized[stage] = _as_int(value, 0)
    return dict(sorted(normalized.items()))


def _normalize_breakdown(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        token = _as_text(key).lower()
        if not token:
            continue
        normalized[token] = _as_int(value, 0)
    return dict(sorted(normalized.items()))


def canonicalize_replay_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    timeline_raw = payload.get("timeline")
    timeline = [item for item in timeline_raw if isinstance(item, dict)] if isinstance(timeline_raw, list) else []

    timeline_items: list[dict[str, Any]] = []
    for index, event in enumerate(timeline, start=1):
        item = {
            "seq": index,
            "stage": _as_text(event.get("stage")).lower() or "unknown",
            "attempt": _as_int(event.get("attempt"), 0) if event.get("attempt") is not None else None,
            "status": _as_text(event.get("status")).lower(),
            "retryable": bool(event.get("retryable")) if "retryable" in event else None,
            "failure_class": _as_text(event.get("failure_class")).lower(),
            "stop_reason": _as_text(event.get("stop_reason")).lower(),
            "message": _as_text(event.get("message")),
        }
        timeline_items.append(item)

    attempt_summary_raw = payload.get("attempt_summary")
    attempts = [item for item in attempt_summary_raw if isinstance(item, dict)] if isinstance(attempt_summary_raw, list) else []
    attempt_items: list[dict[str, Any]] = []
    for item in attempts:
        errors = item.get("errors")
        error_list = [str(err) for err in errors if str(err)] if isinstance(errors, list) else []
        attempt_items.append(
            {
                "attempt": _as_int(item.get("attempt"), 0),
                "stage_counts": _normalize_stage_counts(item.get("stage_counts")),
                "tool_rounds": _as_int(item.get("tool_rounds"), 0),
                "verification_repairs": _as_int(item.get("verification_repairs"), 0),
                "error_count": len(error_list),
                "errors": error_list,
            }
        )
    attempt_items.sort(key=lambda item: int(item.get("attempt") or 0))

    resume_raw = payload.get("resume_snapshots")
    resumes = [item for item in resume_raw if isinstance(item, dict)] if isinstance(resume_raw, list) else []
    resume_items: list[dict[str, Any]] = []
    for item in resumes:
        completed = item.get("completed_steps")
        completed_steps = sorted(str(step) for step in completed if str(step).strip()) if isinstance(completed, list) else []
        resume_items.append(
            {
                "attempt": _as_int(item.get("attempt"), 0),
                "completed_steps": completed_steps,
            }
        )

    issue_summary = payload.get("issue_summary") if isinstance(payload.get("issue_summary"), dict) else {}

    return {
        "schema_version": 1,
        "status": _as_text(payload.get("status")).lower(),
        "stop_reason": _as_text(payload.get("stop_reason")).lower(),
        "failure_class": _as_text(payload.get("failure_class")).lower(),
        "attempts": _as_int(payload.get("attempts"), 0),
        "max_attempts": _as_int(payload.get("max_attempts"), 0),
        "checkpoint_count": len(timeline_items),
        "has_result": bool(payload.get("has_result")),
        "error_message": _as_text(payload.get("error_message")),
        "timeline": timeline_items,
        "attempt_summary": attempt_items,
        "resume_snapshots": resume_items,
        "issue_summary": {
            "count": _as_int(issue_summary.get("count"), 0),
            "status_breakdown": _normalize_breakdown(issue_summary.get("status_breakdown")),
            "artifact_count": _as_int(issue_summary.get("artifact_count"), 0),
            "artifact_breakdown": _normalize_breakdown(issue_summary.get("artifact_breakdown")),
            "tool_call_count": _as_int(issue_summary.get("tool_call_count"), 0),
            "tool_call_status_breakdown": _normalize_breakdown(issue_summary.get("tool_call_status_breakdown")),
        },
    }
