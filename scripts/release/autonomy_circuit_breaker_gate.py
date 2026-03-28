#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate autonomy circuit breaker contract "
            "(docs + runtime API smoke for service controls and run blocking behavior)."
        )
    )
    parser.add_argument(
        "--doc",
        default="docs/autonomy-circuit-breaker.md",
        help="Path to autonomy circuit breaker documentation.",
    )
    parser.add_argument(
        "--token",
        default="dev-token",
        help="User auth token used for runtime checks.",
    )
    parser.add_argument(
        "--service-token",
        default="service-token",
        help="Service auth token used for runtime checks.",
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


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    doc_path = _resolve_path(repo_root, str(args.doc))
    user_token = str(args.token).strip() or "dev-token"
    service_token = str(args.service_token).strip() or "service-token"

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if doc_path.exists():
        text = doc_path.read_text(encoding="utf-8")
        add_check("doc_exists", True, str(doc_path))
        add_check(
            "doc_status_endpoint",
            "/service/runs/autonomy-circuit-breaker" in text,
            "status endpoint documented",
        )
        add_check(
            "doc_update_endpoint",
            "POST /service/runs/autonomy-circuit-breaker" in text,
            "update endpoint documented",
        )
        add_check("doc_actions", "arm|disarm" in text, "arm/disarm action contract documented")
        add_check(
            "doc_execute_blocking",
            "interaction_mode=execute" in text,
            "execute-mode blocking behavior documented",
        )
        add_check(
            "doc_scope_type",
            "scope_type" in text and "global" in text and "user" in text and "agent" in text,
            "scope_type contract documented",
        )
        add_check(
            "doc_timeline_endpoint",
            "/service/runs/autonomy-circuit-breaker/timeline" in text,
            "timeline endpoint documented",
        )
        add_check(
            "doc_restart_restore_policy",
            "restart" in text.lower() and "state" in text.lower(),
            "restart/restore policy documented",
        )
        add_check(
            "doc_recovery_guidance",
            "recovery_guidance" in text,
            "recovery guidance contract documented",
        )
        add_check(
            "doc_automation_breaker_behavior",
            "automation" in text.lower() and "run_blocked_autonomy_circuit_breaker" in text,
            "automation breaker pause behavior documented",
        )
        add_check(
            "doc_supervisor_breaker_behavior",
            "supervisor" in text.lower() and "node_run_blocked_autonomy_circuit_breaker" in text,
            "supervisor breaker pause behavior documented",
        )
    else:
        add_check("doc_exists", False, f"missing: {doc_path}")

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-autonomy-circuit-breaker-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    app = None
    restart_app = None
    restart_agent_id = ""
    supervisor_graph_id = ""
    supervisor_user_scope_graph_id = ""
    supervisor_agent_scope_graph_id = ""

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            user_token: {"user_id": "gate-user", "scopes": ["user"]},
            "gate-user2-token": {"user_id": "gate-user-2", "scopes": ["user"]},
            service_token: {"user_id": "gate-service", "scopes": ["service"]},
            "gate-admin-token": {"user_id": "gate-admin", "scopes": ["admin", "user"]},
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
            status_before = client.get(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
            )
            add_check(
                "runtime_status_endpoint_ok",
                status_before.status_code == 200,
                f"status={status_before.status_code}",
            )
            status_payload = status_before.json() if _is_json_response(dict(status_before.headers)) else {}
            armed_before = bool((status_payload.get("circuit_breaker") or {}).get("armed"))
            add_check(
                "runtime_initially_disarmed",
                not armed_before,
                f"armed={armed_before}",
            )
            add_check(
                "runtime_status_recovery_guidance",
                isinstance((status_payload.get("recovery_guidance") or {}).get("recommendations"), list),
                "status endpoint returns recovery guidance",
            )

            create_agent = client.post(
                "/agents/create",
                headers=_auth(user_token),
                json={
                    "name": "Autonomy Circuit Breaker Gate Agent",
                    "system_prompt": "autonomy-circuit-breaker-gate",
                    "user_id": "gate-user",
                    "tools": ["web_search"],
                },
            )
            add_check(
                "runtime_create_agent_ok",
                create_agent.status_code == 200,
                f"status={create_agent.status_code}",
            )
            create_payload = create_agent.json() if _is_json_response(dict(create_agent.headers)) else {}
            agent_id = str(create_payload.get("id") or "").strip()
            add_check("runtime_create_agent_id", bool(agent_id), f"agent_id={agent_id}")

            create_agent_user2 = client.post(
                "/agents/create",
                headers=_auth("gate-user2-token"),
                json={
                    "name": "Autonomy Circuit Breaker Gate Agent User2",
                    "system_prompt": "autonomy-circuit-breaker-gate",
                    "user_id": "gate-user-2",
                    "tools": ["web_search"],
                },
            )
            add_check(
                "runtime_create_agent_user2_ok",
                create_agent_user2.status_code == 200,
                f"status={create_agent_user2.status_code}",
            )
            create_payload_user2 = (
                create_agent_user2.json() if _is_json_response(dict(create_agent_user2.headers)) else {}
            )
            agent2_id = str(create_payload_user2.get("id") or "").strip()
            add_check("runtime_create_agent_user2_id", bool(agent2_id), f"agent_id={agent2_id}")

            arm = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "arm",
                    "reason": "gate-check",
                    "apply_kill_switch": False,
                },
            )
            add_check(
                "runtime_arm_ok",
                arm.status_code == 200,
                f"status={arm.status_code}",
            )
            arm_payload = arm.json() if _is_json_response(dict(arm.headers)) else {}
            arm_state = arm_payload.get("circuit_breaker") if isinstance(arm_payload, dict) else {}
            add_check(
                "runtime_arm_state",
                bool((arm_state or {}).get("armed")),
                f"armed={bool((arm_state or {}).get('armed'))}",
            )
            add_check(
                "runtime_arm_receipt",
                bool((arm_payload.get("action_receipt") or {}).get("signature")),
                "signed action receipt present",
            )
            armed_status = client.get(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
            )
            armed_status_payload = armed_status.json() if _is_json_response(dict(armed_status.headers)) else {}
            armed_guidance = (
                armed_status_payload.get("recovery_guidance")
                if isinstance(armed_status_payload.get("recovery_guidance"), dict)
                else {}
            )
            add_check(
                "runtime_armed_recovery_guidance",
                armed_status.status_code == 200
                and str((armed_guidance or {}).get("status") or "") in {"action_required", "monitoring"},
                "armed status includes actionable recovery guidance",
            )

            run_blocked = client.post(
                f"/agents/{agent_id}/runs",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "execute while breaker armed",
                },
            )
            blocked_payload = run_blocked.json() if _is_json_response(dict(run_blocked.headers)) else {}
            blocked_error = blocked_payload.get("error") if isinstance(blocked_payload, dict) else {}
            add_check(
                "runtime_execute_create_blocked",
                run_blocked.status_code == 400
                and str((blocked_error or {}).get("type") or "") == "validation_error",
                f"status={run_blocked.status_code}",
            )

            dispatch_blocked = client.post(
                f"/agents/{agent_id}/runs/dispatch",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "dispatch execute while breaker armed",
                    "interaction_mode": "execute",
                },
            )
            add_check(
                "runtime_execute_dispatch_blocked",
                dispatch_blocked.status_code == 400,
                f"status={dispatch_blocked.status_code}",
            )

            dispatch_plan = client.post(
                f"/agents/{agent_id}/runs/dispatch",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "plan while breaker armed",
                    "interaction_mode": "plan",
                },
            )
            plan_payload = dispatch_plan.json() if _is_json_response(dict(dispatch_plan.headers)) else {}
            add_check(
                "runtime_plan_dispatch_allowed",
                dispatch_plan.status_code == 200
                and str(plan_payload.get("interaction_mode") or "") == "plan",
                f"status={dispatch_plan.status_code}",
            )

            automation_create = client.post(
                "/automations/create",
                headers=_auth(user_token),
                json={
                    "agent_id": agent_id,
                    "user_id": "gate-user",
                    "message": "gate breaker automation run",
                    "session_id": "gate-breaker-automation",
                    "interval_sec": 60,
                    "start_immediately": False,
                    "timezone": "UTC",
                },
            )
            add_check(
                "runtime_automation_create_ok",
                automation_create.status_code == 200,
                f"status={automation_create.status_code}",
            )
            automation_payload = (
                automation_create.json() if _is_json_response(dict(automation_create.headers)) else {}
            )
            automation_id = str((automation_payload.get("automation") or {}).get("id") or "").strip()
            add_check("runtime_automation_id", bool(automation_id), f"automation_id={automation_id}")

            if automation_id:
                automation_run_blocked = client.post(
                    f"/automations/{automation_id}/run",
                    headers=_auth(user_token),
                )
                add_check(
                    "runtime_automation_run_blocked_gracefully",
                    automation_run_blocked.status_code == 200,
                    f"status={automation_run_blocked.status_code}",
                )
                automation_run_payload = (
                    automation_run_blocked.json()
                    if _is_json_response(dict(automation_run_blocked.headers))
                    else {}
                )
                automation_row = (
                    automation_run_payload.get("automation")
                    if isinstance(automation_run_payload.get("automation"), dict)
                    else {}
                )
                add_check(
                    "runtime_automation_block_does_not_count_failure",
                    int((automation_row or {}).get("consecutive_failures", 0)) == 0
                    and str((automation_row or {}).get("escalation_level") or "none") == "none"
                    and bool((automation_row or {}).get("is_enabled", False))
                    and (automation_row or {}).get("last_error") in {None, ""},
                    "blocked automation run does not increment failure/escalation",
                )
                automation_events = client.get(
                    f"/automations/{automation_id}/events",
                    headers=_auth(user_token),
                    params={"limit": 100},
                )
                automation_events_payload = (
                    automation_events.json() if _is_json_response(dict(automation_events.headers)) else {}
                )
                automation_event_items = (
                    automation_events_payload.get("items")
                    if isinstance(automation_events_payload.get("items"), list)
                    else []
                )
                blocked_event = next(
                    (
                        item
                        for item in automation_event_items
                        if str(item.get("event_type") or "").strip() == "run_blocked_autonomy_circuit_breaker"
                    ),
                    None,
                )
                add_check(
                    "runtime_automation_block_event_emitted",
                    automation_events.status_code == 200 and isinstance(blocked_event, dict),
                    "automation emits run_blocked_autonomy_circuit_breaker while breaker is armed",
                )

            supervisor_graph_create = client.post(
                "/supervisor/graphs/create",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "objective": "gate supervisor global breaker check",
                    "nodes": [
                        {
                            "node_id": "sup-global-node",
                            "agent_id": agent_id,
                            "message": "supervisor dispatch while breaker armed",
                        }
                    ],
                },
            )
            add_check(
                "runtime_supervisor_graph_create_ok",
                supervisor_graph_create.status_code == 200,
                f"status={supervisor_graph_create.status_code}",
            )
            supervisor_graph_payload = (
                supervisor_graph_create.json()
                if _is_json_response(dict(supervisor_graph_create.headers))
                else {}
            )
            supervisor_graph_id = str((supervisor_graph_payload.get("supervisor_graph") or {}).get("id") or "").strip()
            add_check("runtime_supervisor_graph_id", bool(supervisor_graph_id), f"graph_id={supervisor_graph_id}")
            if supervisor_graph_id:
                supervisor_launch_blocked = client.post(
                    f"/supervisor/graphs/{supervisor_graph_id}/launch",
                    headers=_auth(user_token),
                    json={"session_id": "gate-breaker-supervisor-global"},
                )
                supervisor_launch_payload = (
                    supervisor_launch_blocked.json()
                    if _is_json_response(dict(supervisor_launch_blocked.headers))
                    else {}
                )
                supervisor_graph = (
                    supervisor_launch_payload.get("supervisor_graph")
                    if isinstance(supervisor_launch_payload.get("supervisor_graph"), dict)
                    else {}
                )
                supervisor_nodes = (
                    supervisor_graph.get("nodes") if isinstance(supervisor_graph.get("nodes"), list) else []
                )
                supervisor_node = next(
                    (
                        item
                        for item in supervisor_nodes
                        if isinstance(item, dict) and str(item.get("node_id") or "") == "sup-global-node"
                    ),
                    {},
                )
                add_check(
                    "runtime_supervisor_launch_blocked_gracefully",
                    supervisor_launch_blocked.status_code == 200
                    and str(supervisor_node.get("status") or "") == "planned"
                    and not bool(str(supervisor_node.get("run_id") or "").strip())
                    and str(supervisor_graph.get("status") or "") in {"running", "planned"},
                    f"status={supervisor_launch_blocked.status_code}",
                )
                supervisor_timeline = (
                    supervisor_graph.get("timeline")
                    if isinstance(supervisor_graph.get("timeline"), list)
                    else []
                )
                add_check(
                    "runtime_supervisor_block_event_emitted",
                    any(
                        isinstance(item, dict)
                        and str(item.get("event") or "").strip() == "node_run_blocked_autonomy_circuit_breaker"
                        for item in supervisor_timeline
                    ),
                    "supervisor emits node_run_blocked_autonomy_circuit_breaker while breaker is armed",
                )

            disarm = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "disarm",
                    "reason": "gate-finished",
                },
            )
            disarm_payload = disarm.json() if _is_json_response(dict(disarm.headers)) else {}
            disarm_state = disarm_payload.get("circuit_breaker") if isinstance(disarm_payload, dict) else {}
            add_check(
                "runtime_disarm_ok",
                disarm.status_code == 200,
                f"status={disarm.status_code}",
            )
            add_check(
                "runtime_disarm_state",
                not bool((disarm_state or {}).get("armed")),
                f"armed={bool((disarm_state or {}).get('armed'))}",
            )

            timeline = client.get(
                "/service/runs/autonomy-circuit-breaker/timeline",
                headers=_auth(service_token),
                params={"limit": 200, "transition": "arm"},
            )
            timeline_payload = timeline.json() if _is_json_response(dict(timeline.headers)) else {}
            timeline_items = timeline_payload.get("items") if isinstance(timeline_payload, dict) else []
            timeline_items = timeline_items if isinstance(timeline_items, list) else []
            timeline_match = next(
                (
                    item
                    for item in timeline_items
                    if str(((item.get("transition") or {}).get("reason") or "")).strip() == "gate-check"
                ),
                None,
            )
            add_check(
                "runtime_timeline_endpoint_ok",
                timeline.status_code == 200,
                f"status={timeline.status_code}",
            )
            add_check(
                "runtime_timeline_traceability",
                isinstance(timeline_match, dict)
                and bool(str(timeline_match.get("actor") or "").strip())
                and bool(str(timeline_match.get("request_id") or "").strip()),
                "timeline includes actor/request_id/reason",
            )
            add_check(
                "runtime_timeline_recovery_guidance",
                isinstance((timeline_payload.get("recovery_guidance") or {}).get("recommendations"), list),
                "timeline response includes recovery guidance",
            )

            create_run_after = client.post(
                f"/agents/{agent_id}/runs",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "execute after disarm",
                },
            )
            create_run_payload = (
                create_run_after.json() if _is_json_response(dict(create_run_after.headers)) else {}
            )
            run_payload = create_run_payload.get("run") if isinstance(create_run_payload, dict) else {}
            add_check(
                "runtime_execute_restored_after_disarm",
                create_run_after.status_code == 200 and bool(str((run_payload or {}).get("id") or "").strip()),
                f"status={create_run_after.status_code}",
            )

            if automation_id:
                automation_run_after_disarm = client.post(
                    f"/automations/{automation_id}/run",
                    headers=_auth(user_token),
                )
                automation_run_after_disarm_payload = (
                    automation_run_after_disarm.json()
                    if _is_json_response(dict(automation_run_after_disarm.headers))
                    else {}
                )
                automation_after_row = (
                    automation_run_after_disarm_payload.get("automation")
                    if isinstance(automation_run_after_disarm_payload.get("automation"), dict)
                    else {}
                )
                add_check(
                    "runtime_automation_run_restored_after_disarm",
                    automation_run_after_disarm.status_code == 200
                    and int((automation_after_row or {}).get("consecutive_failures", 0)) == 0,
                    f"status={automation_run_after_disarm.status_code}",
                )
                events_after_disarm = client.get(
                    f"/automations/{automation_id}/events",
                    headers=_auth(user_token),
                    params={"limit": 200},
                )
                events_after_disarm_payload = (
                    events_after_disarm.json()
                    if _is_json_response(dict(events_after_disarm.headers))
                    else {}
                )
                events_after_items = (
                    events_after_disarm_payload.get("items")
                    if isinstance(events_after_disarm_payload.get("items"), list)
                    else []
                )
                add_check(
                    "runtime_automation_run_queued_after_disarm",
                    events_after_disarm.status_code == 200
                    and any(
                        str(item.get("event_type") or "").strip() == "run_queued"
                        for item in events_after_items
                        if isinstance(item, dict)
                    ),
                    "automation run queues successfully after breaker disarm",
                )
            if supervisor_graph_id:
                supervisor_tick_after_disarm = client.post(
                    f"/supervisor/graphs/{supervisor_graph_id}/tick",
                    headers=_auth(user_token),
                    json={"noop": True},
                )
                supervisor_tick_payload = (
                    supervisor_tick_after_disarm.json()
                    if _is_json_response(dict(supervisor_tick_after_disarm.headers))
                    else {}
                )
                supervisor_tick_graph = (
                    supervisor_tick_payload.get("supervisor_graph")
                    if isinstance(supervisor_tick_payload.get("supervisor_graph"), dict)
                    else {}
                )
                supervisor_tick_nodes = (
                    supervisor_tick_graph.get("nodes")
                    if isinstance(supervisor_tick_graph.get("nodes"), list)
                    else []
                )
                supervisor_tick_node = next(
                    (
                        item
                        for item in supervisor_tick_nodes
                        if isinstance(item, dict) and str(item.get("node_id") or "") == "sup-global-node"
                    ),
                    {},
                )
                add_check(
                    "runtime_supervisor_dispatch_restored_after_disarm",
                    supervisor_tick_after_disarm.status_code == 200
                    and bool(str(supervisor_tick_node.get("run_id") or "").strip())
                    and str(supervisor_tick_node.get("status") or "") in {"queued", "running", "succeeded"},
                    f"status={supervisor_tick_after_disarm.status_code}",
                )

            arm_user_scope = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "arm",
                    "scope_type": "user",
                    "scope_user_id": "gate-user",
                    "reason": "gate-user-scope-check",
                    "apply_kill_switch": False,
                },
            )
            add_check(
                "runtime_arm_user_scope_ok",
                arm_user_scope.status_code == 200,
                f"status={arm_user_scope.status_code}",
            )

            create_run_blocked_user_scope = client.post(
                f"/agents/{agent_id}/runs",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "execute while user scope armed",
                },
            )
            add_check(
                "runtime_user_scope_blocks_target_user",
                create_run_blocked_user_scope.status_code == 400,
                f"status={create_run_blocked_user_scope.status_code}",
            )

            create_run_allowed_user2 = client.post(
                f"/agents/{agent2_id}/runs",
                headers=_auth("gate-user2-token"),
                json={
                    "user_id": "gate-user-2",
                    "message": "execute must stay allowed for non-target user",
                },
            )
            add_check(
                "runtime_user_scope_allows_other_user",
                create_run_allowed_user2.status_code == 200,
                f"status={create_run_allowed_user2.status_code}",
            )

            supervisor_user_scope_graph_create = client.post(
                "/supervisor/graphs/create",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "objective": "gate supervisor user scope blocked",
                    "nodes": [
                        {
                            "node_id": "sup-user-node",
                            "agent_id": agent_id,
                            "message": "supervisor user-scoped dispatch",
                        }
                    ],
                },
            )
            add_check(
                "runtime_supervisor_user_scope_graph_create_ok",
                supervisor_user_scope_graph_create.status_code == 200,
                f"status={supervisor_user_scope_graph_create.status_code}",
            )
            supervisor_user_scope_payload = (
                supervisor_user_scope_graph_create.json()
                if _is_json_response(dict(supervisor_user_scope_graph_create.headers))
                else {}
            )
            supervisor_user_scope_graph_id = str(
                (supervisor_user_scope_payload.get("supervisor_graph") or {}).get("id") or ""
            ).strip()
            add_check(
                "runtime_supervisor_user_scope_graph_id",
                bool(supervisor_user_scope_graph_id),
                f"graph_id={supervisor_user_scope_graph_id}",
            )
            if supervisor_user_scope_graph_id:
                supervisor_user_scope_launch = client.post(
                    f"/supervisor/graphs/{supervisor_user_scope_graph_id}/launch",
                    headers=_auth(user_token),
                    json={"session_id": "gate-breaker-supervisor-user-scope"},
                )
                supervisor_user_scope_launch_payload = (
                    supervisor_user_scope_launch.json()
                    if _is_json_response(dict(supervisor_user_scope_launch.headers))
                    else {}
                )
                supervisor_user_scope_graph = (
                    supervisor_user_scope_launch_payload.get("supervisor_graph")
                    if isinstance(supervisor_user_scope_launch_payload.get("supervisor_graph"), dict)
                    else {}
                )
                supervisor_user_scope_nodes = (
                    supervisor_user_scope_graph.get("nodes")
                    if isinstance(supervisor_user_scope_graph.get("nodes"), list)
                    else []
                )
                supervisor_user_scope_node = next(
                    (
                        item
                        for item in supervisor_user_scope_nodes
                        if isinstance(item, dict) and str(item.get("node_id") or "") == "sup-user-node"
                    ),
                    {},
                )
                add_check(
                    "runtime_supervisor_user_scope_blocks_target_user",
                    supervisor_user_scope_launch.status_code == 200
                    and str(supervisor_user_scope_node.get("status") or "") == "planned"
                    and not bool(str(supervisor_user_scope_node.get("run_id") or "").strip()),
                    f"status={supervisor_user_scope_launch.status_code}",
                )

            supervisor_user_scope_allowed_graph_create = client.post(
                "/supervisor/graphs/create",
                headers=_auth("gate-user2-token"),
                json={
                    "user_id": "gate-user-2",
                    "objective": "gate supervisor user scope allowed",
                    "nodes": [
                        {
                            "node_id": "sup-user2-node",
                            "agent_id": agent2_id,
                            "message": "supervisor user2 should stay allowed",
                        }
                    ],
                },
            )
            add_check(
                "runtime_supervisor_user_scope_allowed_graph_create_ok",
                supervisor_user_scope_allowed_graph_create.status_code == 200,
                f"status={supervisor_user_scope_allowed_graph_create.status_code}",
            )
            supervisor_user_scope_allowed_payload = (
                supervisor_user_scope_allowed_graph_create.json()
                if _is_json_response(dict(supervisor_user_scope_allowed_graph_create.headers))
                else {}
            )
            supervisor_user_scope_allowed_graph_id = str(
                (supervisor_user_scope_allowed_payload.get("supervisor_graph") or {}).get("id") or ""
            ).strip()
            if supervisor_user_scope_allowed_graph_id:
                supervisor_user_scope_allowed_launch = client.post(
                    f"/supervisor/graphs/{supervisor_user_scope_allowed_graph_id}/launch",
                    headers=_auth("gate-user2-token"),
                    json={"session_id": "gate-breaker-supervisor-user-scope-allowed"},
                )
                supervisor_user_scope_allowed_launch_payload = (
                    supervisor_user_scope_allowed_launch.json()
                    if _is_json_response(dict(supervisor_user_scope_allowed_launch.headers))
                    else {}
                )
                supervisor_user_scope_allowed_graph = (
                    supervisor_user_scope_allowed_launch_payload.get("supervisor_graph")
                    if isinstance(supervisor_user_scope_allowed_launch_payload.get("supervisor_graph"), dict)
                    else {}
                )
                supervisor_user_scope_allowed_nodes = (
                    supervisor_user_scope_allowed_graph.get("nodes")
                    if isinstance(supervisor_user_scope_allowed_graph.get("nodes"), list)
                    else []
                )
                supervisor_user_scope_allowed_node = next(
                    (
                        item
                        for item in supervisor_user_scope_allowed_nodes
                        if isinstance(item, dict) and str(item.get("node_id") or "") == "sup-user2-node"
                    ),
                    {},
                )
                add_check(
                    "runtime_supervisor_user_scope_allows_other_user",
                    supervisor_user_scope_allowed_launch.status_code == 200
                    and bool(str(supervisor_user_scope_allowed_node.get("run_id") or "").strip()),
                    f"status={supervisor_user_scope_allowed_launch.status_code}",
                )

            disarm_user_scope = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "disarm",
                    "scope_type": "user",
                    "scope_user_id": "gate-user",
                    "reason": "gate-user-scope-finished",
                },
            )
            add_check(
                "runtime_disarm_user_scope_ok",
                disarm_user_scope.status_code == 200,
                f"status={disarm_user_scope.status_code}",
            )
            if supervisor_user_scope_graph_id:
                supervisor_user_scope_tick = client.post(
                    f"/supervisor/graphs/{supervisor_user_scope_graph_id}/tick",
                    headers=_auth(user_token),
                    json={"noop": True},
                )
                supervisor_user_scope_tick_payload = (
                    supervisor_user_scope_tick.json()
                    if _is_json_response(dict(supervisor_user_scope_tick.headers))
                    else {}
                )
                supervisor_user_scope_tick_graph = (
                    supervisor_user_scope_tick_payload.get("supervisor_graph")
                    if isinstance(supervisor_user_scope_tick_payload.get("supervisor_graph"), dict)
                    else {}
                )
                supervisor_user_scope_tick_nodes = (
                    supervisor_user_scope_tick_graph.get("nodes")
                    if isinstance(supervisor_user_scope_tick_graph.get("nodes"), list)
                    else []
                )
                supervisor_user_scope_tick_node = next(
                    (
                        item
                        for item in supervisor_user_scope_tick_nodes
                        if isinstance(item, dict) and str(item.get("node_id") or "") == "sup-user-node"
                    ),
                    {},
                )
                add_check(
                    "runtime_supervisor_user_scope_restored_after_disarm",
                    supervisor_user_scope_tick.status_code == 200
                    and bool(str(supervisor_user_scope_tick_node.get("run_id") or "").strip()),
                    f"status={supervisor_user_scope_tick.status_code}",
                )

            arm_agent_scope = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "arm",
                    "scope_type": "agent",
                    "scope_agent_id": agent_id,
                    "reason": "gate-agent-scope-check",
                    "apply_kill_switch": False,
                },
            )
            add_check(
                "runtime_arm_agent_scope_ok",
                arm_agent_scope.status_code == 200,
                f"status={arm_agent_scope.status_code}",
            )

            create_run_blocked_agent_scope = client.post(
                f"/agents/{agent_id}/runs",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "execute while agent scope armed",
                },
            )
            add_check(
                "runtime_agent_scope_blocks_target_agent",
                create_run_blocked_agent_scope.status_code == 400,
                f"status={create_run_blocked_agent_scope.status_code}",
            )

            create_run_allowed_other_agent = client.post(
                f"/agents/{agent2_id}/runs",
                headers=_auth("gate-user2-token"),
                json={
                    "user_id": "gate-user-2",
                    "message": "execute must stay allowed for non-target agent",
                },
            )
            add_check(
                "runtime_agent_scope_allows_other_agent",
                create_run_allowed_other_agent.status_code == 200,
                f"status={create_run_allowed_other_agent.status_code}",
            )

            supervisor_agent_scope_graph_create = client.post(
                "/supervisor/graphs/create",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "objective": "gate supervisor agent scope blocked",
                    "nodes": [
                        {
                            "node_id": "sup-agent-node",
                            "agent_id": agent_id,
                            "message": "supervisor agent-scoped dispatch",
                        }
                    ],
                },
            )
            add_check(
                "runtime_supervisor_agent_scope_graph_create_ok",
                supervisor_agent_scope_graph_create.status_code == 200,
                f"status={supervisor_agent_scope_graph_create.status_code}",
            )
            supervisor_agent_scope_payload = (
                supervisor_agent_scope_graph_create.json()
                if _is_json_response(dict(supervisor_agent_scope_graph_create.headers))
                else {}
            )
            supervisor_agent_scope_graph_id = str(
                (supervisor_agent_scope_payload.get("supervisor_graph") or {}).get("id") or ""
            ).strip()
            add_check(
                "runtime_supervisor_agent_scope_graph_id",
                bool(supervisor_agent_scope_graph_id),
                f"graph_id={supervisor_agent_scope_graph_id}",
            )
            if supervisor_agent_scope_graph_id:
                supervisor_agent_scope_launch = client.post(
                    f"/supervisor/graphs/{supervisor_agent_scope_graph_id}/launch",
                    headers=_auth(user_token),
                    json={"session_id": "gate-breaker-supervisor-agent-scope"},
                )
                supervisor_agent_scope_launch_payload = (
                    supervisor_agent_scope_launch.json()
                    if _is_json_response(dict(supervisor_agent_scope_launch.headers))
                    else {}
                )
                supervisor_agent_scope_graph = (
                    supervisor_agent_scope_launch_payload.get("supervisor_graph")
                    if isinstance(supervisor_agent_scope_launch_payload.get("supervisor_graph"), dict)
                    else {}
                )
                supervisor_agent_scope_nodes = (
                    supervisor_agent_scope_graph.get("nodes")
                    if isinstance(supervisor_agent_scope_graph.get("nodes"), list)
                    else []
                )
                supervisor_agent_scope_node = next(
                    (
                        item
                        for item in supervisor_agent_scope_nodes
                        if isinstance(item, dict) and str(item.get("node_id") or "") == "sup-agent-node"
                    ),
                    {},
                )
                add_check(
                    "runtime_supervisor_agent_scope_blocks_target_agent",
                    supervisor_agent_scope_launch.status_code == 200
                    and str(supervisor_agent_scope_node.get("status") or "") == "planned"
                    and not bool(str(supervisor_agent_scope_node.get("run_id") or "").strip()),
                    f"status={supervisor_agent_scope_launch.status_code}",
                )

            disarm_agent_scope = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "disarm",
                    "scope_type": "agent",
                    "scope_agent_id": agent_id,
                    "reason": "gate-agent-scope-finished",
                },
            )
            add_check(
                "runtime_disarm_agent_scope_ok",
                disarm_agent_scope.status_code == 200,
                f"status={disarm_agent_scope.status_code}",
            )
            if supervisor_agent_scope_graph_id:
                supervisor_agent_scope_tick = client.post(
                    f"/supervisor/graphs/{supervisor_agent_scope_graph_id}/tick",
                    headers=_auth(user_token),
                    json={"noop": True},
                )
                supervisor_agent_scope_tick_payload = (
                    supervisor_agent_scope_tick.json()
                    if _is_json_response(dict(supervisor_agent_scope_tick.headers))
                    else {}
                )
                supervisor_agent_scope_tick_graph = (
                    supervisor_agent_scope_tick_payload.get("supervisor_graph")
                    if isinstance(supervisor_agent_scope_tick_payload.get("supervisor_graph"), dict)
                    else {}
                )
                supervisor_agent_scope_tick_nodes = (
                    supervisor_agent_scope_tick_graph.get("nodes")
                    if isinstance(supervisor_agent_scope_tick_graph.get("nodes"), list)
                    else []
                )
                supervisor_agent_scope_tick_node = next(
                    (
                        item
                        for item in supervisor_agent_scope_tick_nodes
                        if isinstance(item, dict) and str(item.get("node_id") or "") == "sup-agent-node"
                    ),
                    {},
                )
                add_check(
                    "runtime_supervisor_agent_scope_restored_after_disarm",
                    supervisor_agent_scope_tick.status_code == 200
                    and bool(str(supervisor_agent_scope_tick_node.get("run_id") or "").strip()),
                    f"status={supervisor_agent_scope_tick.status_code}",
                )

            arm_for_restart = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "arm",
                    "scope_type": "global",
                    "reason": "restart-restore-check",
                    "apply_kill_switch": False,
                },
            )
            add_check(
                "runtime_arm_for_restart_ok",
                arm_for_restart.status_code == 200,
                f"status={arm_for_restart.status_code}",
            )
            restart_agent_id = agent_id

        restart_app = create_app()
        with TestClient(restart_app) as restarted_client:
            restarted_status = restarted_client.get(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
            )
            restarted_payload = (
                restarted_status.json() if _is_json_response(dict(restarted_status.headers)) else {}
            )
            restarted_state = (
                restarted_payload.get("circuit_breaker") if isinstance(restarted_payload, dict) else {}
            )
            add_check(
                "runtime_restart_restores_armed_state",
                restarted_status.status_code == 200 and bool((restarted_state or {}).get("armed")),
                f"status={restarted_status.status_code}",
            )

            if restart_agent_id:
                run_blocked_after_restart = restarted_client.post(
                    f"/agents/{restart_agent_id}/runs",
                    headers=_auth(user_token),
                    json={
                        "user_id": "gate-user",
                        "message": "execute while breaker armed after restart",
                    },
                )
                add_check(
                    "runtime_restart_execute_create_blocked",
                    run_blocked_after_restart.status_code == 400,
                    f"status={run_blocked_after_restart.status_code}",
                )

            disarm_after_restart = restarted_client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "disarm",
                    "scope_type": "global",
                    "reason": "restart-restore-finished",
                },
            )
            add_check(
                "runtime_restart_disarm_ok",
                disarm_after_restart.status_code == 200,
                f"status={disarm_after_restart.status_code}",
            )

    except Exception as exc:  # pragma: no cover - integration fallback
        add_check("runtime_exception", False, str(exc))
    finally:
        if app is not None:
            _shutdown_app(app)
        if restart_app is not None:
            _shutdown_app(restart_app)
        tmp_dir.cleanup()

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "suite": "autonomy_circuit_breaker_gate_v1",
        "generated_at": _utc_now_iso(),
        "checks": checks,
        "summary": {
            "status": "pass" if not failed else "fail",
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
    }

    output = str(args.output or "").strip()
    if output:
        _write_json(_resolve_path(repo_root, output), report)

    if failed:
        names = ", ".join(str(item.get("name")) for item in failed)
        print(f"[autonomy-circuit-breaker-gate] FAILED checks={names}")
        return 1

    print("[autonomy-circuit-breaker-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
