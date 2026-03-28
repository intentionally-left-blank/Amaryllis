#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate action timeline stream + plain-language explainability contract "
            "(docs + runtime API smoke for /agents/runs/{run_id}/events and /agents/runs/{run_id}/explain)."
        )
    )
    parser.add_argument(
        "--timeline-doc",
        default="docs/mission-audit-timeline.md",
        help="Path to mission audit timeline documentation.",
    )
    parser.add_argument(
        "--explain-doc",
        default="docs/agent-run-explainability-feed.md",
        help="Path to explainability feed documentation.",
    )
    parser.add_argument(
        "--token",
        default="dev-token",
        help="Auth token used for runtime checks.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _shutdown_app(app: object) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    try:
        services.automation_scheduler.stop()
        if services.memory_consolidation_worker is not None:
            services.memory_consolidation_worker.stop()
        if services.backup_scheduler is not None:
            services.backup_scheduler.stop()
        services.agent_run_manager.stop()
        services.database.close()
        services.vector_store.persist()
    except Exception:
        pass


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _is_json_response(headers: dict[str, Any]) -> bool:
    return str(headers.get("content-type") or "").startswith("application/json")


def _wait_terminal_run(*, client: Any, token: str, run_id: str, timeout_sec: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/agents/runs/{run_id}", headers=_auth(token))
        if response.status_code == 200:
            payload = response.json().get("run", {})
            latest = payload if isinstance(payload, dict) else {}
            status = str(latest.get("status") or "").strip().lower()
            if status in {"succeeded", "failed", "canceled"}:
                return latest
        time.sleep(0.05)
    raise ValueError(f"Run did not reach terminal status in time: run_id={run_id}, latest={latest}")


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    timeline_doc = _resolve_path(repo_root, str(args.timeline_doc))
    explain_doc = _resolve_path(repo_root, str(args.explain_doc))
    token = str(args.token).strip() or "dev-token"

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if timeline_doc.exists():
        text = timeline_doc.read_text(encoding="utf-8")
        add_check("timeline_doc_exists", True, str(timeline_doc))
        add_check(
            "timeline_doc_events_endpoint",
            "/agents/runs/{run_id}/events" in text,
            "timeline stream endpoint documented",
        )
        add_check(
            "timeline_doc_audit_endpoint",
            "/agents/runs/{run_id}/audit" in text,
            "audit endpoint documented",
        )
        add_check(
            "timeline_doc_export_endpoint",
            "/agents/runs/{run_id}/audit/export" in text,
            "audit export endpoint documented",
        )
    else:
        add_check("timeline_doc_exists", False, f"missing: {timeline_doc}")

    if explain_doc.exists():
        text = explain_doc.read_text(encoding="utf-8")
        add_check("explain_doc_exists", True, str(explain_doc))
        add_check(
            "explain_doc_explain_endpoint",
            "/agents/runs/{run_id}/explain" in text,
            "explain endpoint documented",
        )
        add_check("explain_doc_reason_field", "reason" in text, "reason field documented")
        add_check("explain_doc_result_field", "result" in text, "result field documented")
        add_check("explain_doc_next_step_field", "next_step" in text, "next_step field documented")
    else:
        add_check("explain_doc_exists", False, f"missing: {explain_doc}")

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-action-explainability-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    app = None

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            token: {"user_id": "explain-user", "scopes": ["user"]},
            "explain-admin-token": {"user_id": "explain-admin", "scopes": ["admin", "user"]},
            "explain-other-token": {"user_id": "explain-other", "scopes": ["user"]},
            "explain-service-token": {"user_id": "explain-service", "scopes": ["service"]},
        },
        ensure_ascii=False,
    )
    os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
    os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
    os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
    os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
    os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"
    os.environ["AMARYLLIS_AUTOMATION_ENABLED"] = "false"
    os.environ["AMARYLLIS_BACKUP_ENABLED"] = "false"
    os.environ["AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED"] = "false"
    os.environ["AMARYLLIS_REQUEST_TRACE_LOGS_ENABLED"] = "false"

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from runtime.server import create_app  # noqa: PLC0415

        app = create_app()
        with TestClient(app) as client:
            create_agent_resp = client.post(
                "/agents/create",
                headers=_auth(token),
                json={
                    "name": "Action Explainability Gate Agent",
                    "system_prompt": "action-explainability-gate",
                    "user_id": "explain-user",
                    "tools": ["web_search"],
                },
            )
            add_check(
                "runtime_create_agent_ok",
                create_agent_resp.status_code == 200,
                f"status={create_agent_resp.status_code}",
            )
            agent_payload = create_agent_resp.json() if _is_json_response(dict(create_agent_resp.headers)) else {}
            agent_id = str(agent_payload.get("id") or "").strip()
            add_check("runtime_create_agent_id", bool(agent_id), f"agent_id={agent_id}")

            create_run_resp = client.post(
                f"/agents/{agent_id}/runs",
                headers=_auth(token),
                json={
                    "user_id": "explain-user",
                    "session_id": "explainability-gate-session",
                    "message": "Collect context and summarize.",
                    "max_attempts": 1,
                },
            )
            add_check(
                "runtime_create_run_ok",
                create_run_resp.status_code == 200,
                f"status={create_run_resp.status_code}",
            )
            run_payload = create_run_resp.json() if _is_json_response(dict(create_run_resp.headers)) else {}
            run = run_payload.get("run") if isinstance(run_payload, dict) else {}
            run_id = str((run or {}).get("id") or "").strip()
            add_check("runtime_create_run_id", bool(run_id), f"run_id={run_id}")

            if run_id:
                terminal = _wait_terminal_run(client=client, token=token, run_id=run_id, timeout_sec=8.0)
                terminal_status = str(terminal.get("status") or "").strip().lower()
                add_check(
                    "runtime_terminal_status",
                    terminal_status in {"succeeded", "failed", "canceled"},
                    f"terminal_status={terminal_status}",
                )

                events_resp = client.get(
                    f"/agents/runs/{run_id}/events",
                    headers=_auth(token),
                    params={
                        "from_index": 0,
                        "poll_interval_ms": 100,
                        "timeout_sec": 2,
                        "include_snapshot": "true",
                        "include_heartbeat": "false",
                    },
                )
                add_check("runtime_events_endpoint_ok", events_resp.status_code == 200, f"status={events_resp.status_code}")
                events_text = str(events_resp.text or "")
                done_present = '"event": "done"' in events_text or '"event":"done"' in events_text
                add_check("runtime_events_done_present", done_present, "SSE stream contains terminal done event")

                audit_resp = client.get(f"/agents/runs/{run_id}/audit", headers=_auth(token))
                add_check("runtime_audit_endpoint_ok", audit_resp.status_code == 200, f"status={audit_resp.status_code}")

                explain_resp = client.get(
                    f"/agents/runs/{run_id}/explain",
                    headers=_auth(token),
                )
                add_check(
                    "runtime_explain_endpoint_ok",
                    explain_resp.status_code == 200,
                    f"status={explain_resp.status_code}",
                )
                explain_payload = explain_resp.json() if _is_json_response(dict(explain_resp.headers)) else {}
                explainability = explain_payload.get("explainability") if isinstance(explain_payload, dict) else {}
                feed_version = str((explainability or {}).get("feed_version") or "").strip()
                add_check(
                    "runtime_explain_feed_version",
                    feed_version == "run_explainability_feed_v1",
                    f"feed_version={feed_version}",
                )
                items = (explainability or {}).get("items") if isinstance(explainability, dict) else []
                add_check(
                    "runtime_explain_items_non_empty",
                    isinstance(items, list) and len(items) >= 1,
                    f"items_count={len(items) if isinstance(items, list) else 'n/a'}",
                )
                if isinstance(items, list):
                    complete_fields = True
                    for item in items:
                        if not isinstance(item, dict):
                            complete_fields = False
                            break
                        reason = str(item.get("reason") or "").strip()
                        result = str(item.get("result") or "").strip()
                        next_step = str(item.get("next_step") or "").strip()
                        if not reason or not result or not next_step:
                            complete_fields = False
                            break
                    add_check(
                        "runtime_explain_items_have_reason_result_next_step",
                        complete_fields,
                        "all explain items expose reason/result/next_step",
                    )
                denied_resp = client.get(
                    f"/agents/runs/{run_id}/explain",
                    headers=_auth("explain-other-token"),
                )
                add_check(
                    "runtime_explain_owner_enforced",
                    denied_resp.status_code == 403,
                    f"status={denied_resp.status_code}",
                )
    except Exception as exc:  # pragma: no cover - fallback diagnostics for CI
        add_check("runtime_gate_execution", False, f"{type(exc).__name__}: {exc}")
    finally:
        if app is not None:
            _shutdown_app(app)
        tmp_dir.cleanup()

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "action_explainability_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "timeline_doc": str(timeline_doc),
            "explain_doc": str(explain_doc),
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if failed:
        print("[action-explainability-gate] FAILED")
        for item in failed:
            print(f"- {item.get('name')}: {item.get('detail')}")
        return 1

    print(f"[action-explainability-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
