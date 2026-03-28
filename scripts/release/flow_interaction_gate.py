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
            "Validate unified flow session and plan-vs-execute interaction contract "
            "(docs + runtime API smoke for /flow/sessions/* and /agents/*/runs/dispatch)."
        )
    )
    parser.add_argument(
        "--flow-doc",
        default="docs/flow-session-contract.md",
        help="Path to unified flow session contract doc.",
    )
    parser.add_argument(
        "--interaction-doc",
        default="docs/agent-run-interaction-modes.md",
        help="Path to plan-vs-execute interaction modes contract doc.",
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


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    flow_doc = _resolve_path(repo_root, str(args.flow_doc))
    interaction_doc = _resolve_path(repo_root, str(args.interaction_doc))
    token = str(args.token).strip() or "dev-token"

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if flow_doc.exists():
        text = flow_doc.read_text(encoding="utf-8")
        add_check("flow_doc_exists", True, str(flow_doc))
        add_check("flow_doc_contract_endpoint", "/flow/sessions/contract" in text, "flow contract endpoint")
        add_check("flow_doc_start_endpoint", "/flow/sessions/start" in text, "flow start endpoint")
        add_check(
            "flow_doc_transition_endpoint",
            "/flow/sessions/{session_id}/transition" in text,
            "flow transition endpoint",
        )
        add_check(
            "flow_doc_activity_endpoint",
            "/flow/sessions/{session_id}/activity" in text,
            "flow activity endpoint",
        )
        add_check(
            "flow_doc_states_present",
            all(state in text for state in ("listening", "planning", "acting", "reviewing", "closed")),
            "flow states include listening/planning/acting/reviewing/closed",
        )
        add_check(
            "flow_doc_channels_present",
            all(channel in text for channel in ("text", "voice", "visual")),
            "flow channels include text/voice/visual",
        )
    else:
        add_check("flow_doc_exists", False, f"missing: {flow_doc}")

    if interaction_doc.exists():
        text = interaction_doc.read_text(encoding="utf-8")
        add_check("interaction_doc_exists", True, str(interaction_doc))
        add_check(
            "interaction_doc_modes_endpoint",
            "/agents/runs/interaction-modes" in text,
            "interaction-modes endpoint",
        )
        add_check(
            "interaction_doc_dispatch_endpoint",
            "/agents/{agent_id}/runs/dispatch" in text,
            "dispatch endpoint",
        )
        add_check(
            "interaction_doc_plan_execute_modes",
            all(mode in text for mode in ("plan", "execute")),
            "plan + execute modes documented",
        )
        add_check("interaction_doc_execute_hint", "execute_hint" in text, "execute hint documented")
        add_check("interaction_doc_trust_boundary", "trust_boundary" in text, "trust boundary documented")
    else:
        add_check("interaction_doc_exists", False, f"missing: {interaction_doc}")

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-flow-interaction-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    app = None

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            token: {"user_id": "flow-user", "scopes": ["user"]},
            "flow-admin-token": {"user_id": "flow-admin", "scopes": ["admin", "user"]},
            "flow-service-token": {"user_id": "flow-service", "scopes": ["service"]},
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
            contract_resp = client.get("/flow/sessions/contract", headers=_auth(token))
            add_check("runtime_flow_contract_ok", contract_resp.status_code == 200, f"status={contract_resp.status_code}")
            contract_payload = contract_resp.json() if _is_json_response(dict(contract_resp.headers)) else {}
            states = set(str(item) for item in (contract_payload.get("states") or []))
            channels = set(str(item) for item in (contract_payload.get("channels") or []))
            required_states = {"created", "listening", "planning", "acting", "reviewing", "closed"}
            required_channels = {"text", "voice", "visual"}
            add_check(
                "runtime_flow_contract_states",
                required_states.issubset(states),
                f"states={sorted(states)}",
            )
            add_check(
                "runtime_flow_contract_channels",
                required_channels.issubset(channels),
                f"channels={sorted(channels)}",
            )

            start_resp = client.post(
                "/flow/sessions/start",
                headers=_auth(token),
                json={
                    "user_id": "flow-user",
                    "channels": ["text", "voice", "visual"],
                    "initial_state": "listening",
                    "metadata": {"source": "flow-interaction-gate"},
                },
            )
            add_check("runtime_flow_start_ok", start_resp.status_code == 200, f"status={start_resp.status_code}")
            start_payload = start_resp.json() if _is_json_response(dict(start_resp.headers)) else {}
            flow_session = start_payload.get("flow_session") if isinstance(start_payload, dict) else {}
            session_id = str((flow_session or {}).get("id") or "").strip()
            add_check("runtime_flow_session_id", bool(session_id), f"session_id={session_id}")
            add_check(
                "runtime_flow_initial_state",
                str((flow_session or {}).get("state") or "") == "listening",
                f"state={str((flow_session or {}).get('state') or '')}",
            )

            for to_state in ("planning", "acting", "reviewing"):
                transition_resp = client.post(
                    f"/flow/sessions/{session_id}/transition",
                    headers=_auth(token),
                    json={"to_state": to_state, "reason": f"{to_state}_requested"},
                )
                add_check(
                    f"runtime_flow_transition_{to_state}",
                    transition_resp.status_code == 200,
                    f"status={transition_resp.status_code}",
                )

            text_activity_resp = client.post(
                f"/flow/sessions/{session_id}/activity",
                headers=_auth(token),
                json={"channel": "text", "event": "prompt_submitted"},
            )
            add_check(
                "runtime_flow_activity_text_ok",
                text_activity_resp.status_code == 200,
                f"status={text_activity_resp.status_code}",
            )
            voice_activity_resp = client.post(
                f"/flow/sessions/{session_id}/activity",
                headers=_auth(token),
                json={"channel": "voice", "event": "audio_chunk"},
            )
            add_check(
                "runtime_flow_activity_voice_ok",
                voice_activity_resp.status_code == 200,
                f"status={voice_activity_resp.status_code}",
            )

            get_resp = client.get(f"/flow/sessions/{session_id}", headers=_auth(token))
            add_check("runtime_flow_get_ok", get_resp.status_code == 200, f"status={get_resp.status_code}")
            get_payload = get_resp.json() if _is_json_response(dict(get_resp.headers)) else {}
            current = get_payload.get("flow_session") if isinstance(get_payload, dict) else {}
            current_state = str((current or {}).get("state") or "")
            add_check(
                "runtime_flow_current_state_reviewing",
                current_state == "reviewing",
                f"state={current_state}",
            )
            current_channels = sorted(str(item) for item in ((current or {}).get("channels") or []))
            add_check(
                "runtime_flow_channels_preserved",
                current_channels == ["text", "visual", "voice"],
                f"channels={current_channels}",
            )
            transitions = (current or {}).get("transitions") or []
            add_check(
                "runtime_flow_transition_history_present",
                isinstance(transitions, list) and len(transitions) >= 5,
                f"transition_count={len(transitions) if isinstance(transitions, list) else 'n/a'}",
            )
            channel_activity = (current or {}).get("channel_activity") if isinstance(current, dict) else {}
            text_events = int((((channel_activity or {}).get("text") or {}).get("events_count") or 0))
            voice_events = int((((channel_activity or {}).get("voice") or {}).get("events_count") or 0))
            add_check("runtime_flow_text_activity_count", text_events >= 1, f"text_events={text_events}")
            add_check("runtime_flow_voice_activity_count", voice_events >= 1, f"voice_events={voice_events}")

            close_resp = client.post(
                f"/flow/sessions/{session_id}/transition",
                headers=_auth(token),
                json={"to_state": "closed", "reason": "gate_complete"},
            )
            add_check("runtime_flow_transition_closed", close_resp.status_code == 200, f"status={close_resp.status_code}")

            create_agent_resp = client.post(
                "/agents/create",
                headers=_auth(token),
                json={
                    "name": "Flow Interaction Gate Agent",
                    "system_prompt": "flow-interaction-gate",
                    "user_id": "flow-user",
                    "tools": ["web_search"],
                },
            )
            add_check(
                "runtime_interaction_create_agent_ok",
                create_agent_resp.status_code == 200,
                f"status={create_agent_resp.status_code}",
            )
            create_agent_payload = (
                create_agent_resp.json() if _is_json_response(dict(create_agent_resp.headers)) else {}
            )
            agent_id = str(create_agent_payload.get("id") or "").strip()
            add_check("runtime_interaction_agent_id", bool(agent_id), f"agent_id={agent_id}")

            modes_resp = client.get("/agents/runs/interaction-modes", headers=_auth(token))
            add_check("runtime_interaction_modes_ok", modes_resp.status_code == 200, f"status={modes_resp.status_code}")
            modes_payload = modes_resp.json() if _is_json_response(dict(modes_resp.headers)) else {}
            supported_modes = set(str(item) for item in (modes_payload.get("supported_interaction_modes") or []))
            add_check(
                "runtime_interaction_modes_supported",
                {"plan", "execute"}.issubset(supported_modes),
                f"supported={sorted(supported_modes)}",
            )

            plan_dispatch_resp = client.post(
                f"/agents/{agent_id}/runs/dispatch",
                headers=_auth(token),
                json={
                    "user_id": "flow-user",
                    "message": "Plan a safe multi-step action",
                    "session_id": "flow-interaction-gate-session",
                    "interaction_mode": "plan",
                    "max_attempts": 2,
                },
            )
            add_check(
                "runtime_interaction_dispatch_plan_ok",
                plan_dispatch_resp.status_code == 200,
                f"status={plan_dispatch_resp.status_code}",
            )
            plan_dispatch_payload = (
                plan_dispatch_resp.json() if _is_json_response(dict(plan_dispatch_resp.headers)) else {}
            )
            plan_mode = str(plan_dispatch_payload.get("interaction_mode") or "").strip()
            plan_execution = bool(
                ((plan_dispatch_payload.get("trust_boundary") or {}).get("execution_performed"))
            )
            execute_hint_mode = str(
                (((plan_dispatch_payload.get("execute_hint") or {}).get("payload") or {}).get("interaction_mode") or "")
            ).strip()
            add_check("runtime_interaction_plan_mode", plan_mode == "plan", f"interaction_mode={plan_mode}")
            add_check(
                "runtime_interaction_plan_is_dry_run",
                not plan_execution,
                f"execution_performed={plan_execution}",
            )
            add_check(
                "runtime_interaction_plan_execute_hint",
                execute_hint_mode == "execute",
                f"execute_hint_mode={execute_hint_mode}",
            )

            runs_after_plan_resp = client.get(
                f"/agents/{agent_id}/runs",
                headers=_auth(token),
                params={"user_id": "flow-user"},
            )
            add_check(
                "runtime_interaction_runs_after_plan_ok",
                runs_after_plan_resp.status_code == 200,
                f"status={runs_after_plan_resp.status_code}",
            )
            runs_after_plan_payload = (
                runs_after_plan_resp.json() if _is_json_response(dict(runs_after_plan_resp.headers)) else {}
            )
            runs_count_after_plan = int(runs_after_plan_payload.get("count") or 0)
            add_check(
                "runtime_interaction_plan_does_not_create_run",
                runs_count_after_plan == 0,
                f"count={runs_count_after_plan}",
            )

            execute_dispatch_resp = client.post(
                f"/agents/{agent_id}/runs/dispatch",
                headers=_auth(token),
                json={
                    "user_id": "flow-user",
                    "message": "Execute now",
                    "interaction_mode": "execute",
                },
            )
            add_check(
                "runtime_interaction_dispatch_execute_ok",
                execute_dispatch_resp.status_code == 200,
                f"status={execute_dispatch_resp.status_code}",
            )
            execute_dispatch_payload = (
                execute_dispatch_resp.json() if _is_json_response(dict(execute_dispatch_resp.headers)) else {}
            )
            execute_mode = str(execute_dispatch_payload.get("interaction_mode") or "").strip()
            execute_trust = bool(
                ((execute_dispatch_payload.get("trust_boundary") or {}).get("execution_performed"))
            )
            run_id = str(((execute_dispatch_payload.get("run") or {}).get("id") or "")).strip()
            add_check("runtime_interaction_execute_mode", execute_mode == "execute", f"interaction_mode={execute_mode}")
            add_check(
                "runtime_interaction_execute_trust_boundary",
                execute_trust,
                f"execution_performed={execute_trust}",
            )
            add_check("runtime_interaction_execute_run_id", bool(run_id), f"run_id={run_id}")

            runs_after_execute_resp = client.get(
                f"/agents/{agent_id}/runs",
                headers=_auth(token),
                params={"user_id": "flow-user"},
            )
            add_check(
                "runtime_interaction_runs_after_execute_ok",
                runs_after_execute_resp.status_code == 200,
                f"status={runs_after_execute_resp.status_code}",
            )
            runs_after_execute_payload = (
                runs_after_execute_resp.json() if _is_json_response(dict(runs_after_execute_resp.headers)) else {}
            )
            runs_count_after_execute = int(runs_after_execute_payload.get("count") or 0)
            add_check(
                "runtime_interaction_execute_creates_run",
                runs_count_after_execute >= 1,
                f"count={runs_count_after_execute}",
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
        "suite": "flow_interaction_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "flow_doc": str(flow_doc),
            "interaction_doc": str(interaction_doc),
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if failed:
        print("[flow-interaction-gate] FAILED")
        for item in failed:
            print(f"- {item.get('name')}: {item.get('detail')}")
        return 1

    print(f"[flow-interaction-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
