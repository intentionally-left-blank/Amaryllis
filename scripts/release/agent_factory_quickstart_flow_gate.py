#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one-phrase quickstart flow contract parity across "
            "/agents/quickstart/plan, /agents/quickstart apply, and "
            "chat intent quick_action creation path."
        )
    )
    parser.add_argument(
        "--fixture",
        default="eval/fixtures/agent_factory/quickstart_flow_cases.json",
        help="Path to quickstart flow fixture suite.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="Minimum pass rate required in [0, 1].",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report path.",
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


def _normalize_text(value: Any, *, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
    return sorted(set(normalized))


def _normalize_source_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"mode": "", "channels": [], "domains": []}
    return {
        "mode": _normalize_text(value.get("mode")),
        "channels": _normalize_string_list(value.get("channels")),
        "domains": _normalize_string_list(value.get("domains")),
    }


def _normalize_schedule(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key in ("interval_hours", "hour", "minute"):
        if key in value:
            try:
                normalized[key] = int(value.get(key))
            except Exception:
                normalized[key] = value.get(key)
    if "byday" in value:
        normalized["byday"] = _normalize_string_list(value.get("byday"))
    return normalized


def _normalize_automation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"enabled": False, "schedule_type": "", "schedule": {}, "timezone": "", "start_immediately": False}
    return {
        "enabled": True,
        "schedule_type": _normalize_text(value.get("schedule_type")),
        "schedule": _normalize_schedule(value.get("schedule")),
        "timezone": _normalize_text(value.get("timezone"), default="UTC"),
        "start_immediately": bool(value.get("start_immediately", False)),
    }


def _canonical_from_plan(value: Any) -> dict[str, Any]:
    plan = value if isinstance(value, dict) else {}
    return {
        "kind": _normalize_text(plan.get("kind"), default="general"),
        "name": _normalize_text(plan.get("name"), default="Custom Assistant"),
        "focus": _normalize_text(plan.get("focus"), default="general"),
        "tools": _normalize_string_list(plan.get("tools")),
        "sources": _normalize_string_list(plan.get("sources")),
        "source_policy": _normalize_source_policy(plan.get("source_policy")),
        "automation": _normalize_automation(plan.get("automation")),
    }


def _canonical_from_spec(value: Any) -> dict[str, Any]:
    spec = value if isinstance(value, dict) else {}
    return {
        "kind": _normalize_text(spec.get("kind"), default="general"),
        "name": _normalize_text(spec.get("name"), default="Custom Assistant"),
        "focus": _normalize_text(spec.get("focus"), default="general"),
        "tools": _normalize_string_list(spec.get("tools")),
        "sources": _normalize_string_list(spec.get("source_targets")),
        "source_policy": _normalize_source_policy(spec.get("source_policy")),
        "automation": _normalize_automation(spec.get("automation")),
    }


def _agents_count(client: Any, token: str, user_id: str) -> tuple[int | None, str]:
    response = client.get(
        "/agents",
        headers=_auth(token),
        params={"user_id": user_id},
    )
    if response.status_code != 200:
        return None, f"status={response.status_code}"
    payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(payload, dict):
        return None, "payload_not_dict"
    try:
        return int(payload.get("count", 0)), ""
    except Exception:
        return None, f"invalid_count={payload.get('count')}"


def _ensure_reason_view(value: Any) -> bool:
    return isinstance(value, dict) and bool(str(value.get("version") or "").strip())


def _evaluate_case(
    *,
    client: Any,
    token: str,
    case_index: int,
    raw_case: dict[str, Any],
) -> dict[str, Any]:
    case_id = _normalize_text(raw_case.get("id"), default=f"case-{case_index + 1}") or f"case-{case_index + 1}"
    request = _normalize_text(raw_case.get("request"))
    expected = raw_case.get("expected", {})
    if not isinstance(expected, dict):
        expected = {}

    mismatches: list[dict[str, Any]] = []

    def add_mismatch(field: str, expected_value: Any, actual: Any) -> None:
        mismatches.append({"field": field, "expected": expected_value, "actual": actual})

    if not request:
        add_mismatch("request", "non-empty", "")
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }

    plan_user_id = f"flow-plan-{case_id}"
    chat_user_id = f"flow-chat-{case_id}"
    session_id = f"flow-chat-session-{case_id}"

    plan_before_count, plan_before_error = _agents_count(client, token, plan_user_id)
    if plan_before_count is None:
        add_mismatch("plan.before_count", "int", plan_before_error)

    plan_response = client.post(
        "/v1/agents/quickstart/plan",
        headers=_auth(token),
        json={"user_id": plan_user_id, "request": request},
    )
    if plan_response.status_code != 200:
        add_mismatch("plan.status_code", 200, int(plan_response.status_code))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    plan_payload = plan_response.json() if plan_response.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(plan_payload, dict):
        add_mismatch("plan.payload", "dict", str(type(plan_payload)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    quickstart_plan = plan_payload.get("quickstart_plan")
    if not isinstance(quickstart_plan, dict):
        add_mismatch("plan.quickstart_plan", "dict", str(type(quickstart_plan)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    if not _ensure_reason_view(quickstart_plan.get("inference_reason_view")):
        add_mismatch("plan.quickstart_plan.inference_reason_view", "dict with version", quickstart_plan.get("inference_reason_view"))

    apply_hint = plan_payload.get("apply_hint")
    apply_payload: dict[str, Any] = {}
    if isinstance(apply_hint, dict) and isinstance(apply_hint.get("payload"), dict):
        apply_payload = dict(apply_hint.get("payload") or {})
    else:
        add_mismatch("plan.apply_hint.payload", "dict", str(type(apply_hint)))
    apply_payload["user_id"] = plan_user_id
    if not _normalize_text(apply_payload.get("request")):
        apply_payload["request"] = request
    if not _normalize_text(apply_payload.get("idempotency_key")):
        add_mismatch("plan.apply_hint.payload.idempotency_key", "non-empty", "")

    plan_after_count, plan_after_error = _agents_count(client, token, plan_user_id)
    if plan_after_count is None:
        add_mismatch("plan.after_count", "int", plan_after_error)
    elif plan_before_count is not None and plan_after_count != plan_before_count:
        add_mismatch("plan.side_effect_count_delta", 0, plan_after_count - plan_before_count)

    apply_first = client.post(
        "/v1/agents/quickstart",
        headers=_auth(token),
        json=apply_payload,
    )
    if apply_first.status_code != 200:
        add_mismatch("apply.first_status_code", 200, int(apply_first.status_code))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    apply_first_payload = (
        apply_first.json() if apply_first.headers.get("content-type", "").startswith("application/json") else {}
    )
    if not isinstance(apply_first_payload, dict):
        add_mismatch("apply.first_payload", "dict", str(type(apply_first_payload)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    apply_spec = apply_first_payload.get("quickstart_spec")
    if not isinstance(apply_spec, dict):
        add_mismatch("apply.quickstart_spec", "dict", str(type(apply_spec)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    if not _ensure_reason_view(apply_spec.get("inference_reason_view")):
        add_mismatch("apply.quickstart_spec.inference_reason_view", "dict with version", apply_spec.get("inference_reason_view"))
    apply_first_agent = apply_first_payload.get("agent")
    if not isinstance(apply_first_agent, dict) or not _normalize_text(apply_first_agent.get("id")):
        add_mismatch("apply.first_agent.id", "non-empty", apply_first_agent)
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }

    apply_second = client.post(
        "/v1/agents/quickstart",
        headers=_auth(token),
        json=apply_payload,
    )
    if apply_second.status_code != 200:
        add_mismatch("apply.second_status_code", 200, int(apply_second.status_code))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    apply_second_payload = (
        apply_second.json() if apply_second.headers.get("content-type", "").startswith("application/json") else {}
    )
    if not isinstance(apply_second_payload, dict):
        add_mismatch("apply.second_payload", "dict", str(type(apply_second_payload)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    apply_second_agent = apply_second_payload.get("agent")
    if not isinstance(apply_second_agent, dict):
        add_mismatch("apply.second_agent", "dict", str(type(apply_second_agent)))
    else:
        first_agent_id = _normalize_text(apply_first_agent.get("id"))
        second_agent_id = _normalize_text(apply_second_agent.get("id"))
        if second_agent_id != first_agent_id:
            add_mismatch("apply.idempotency.agent_id", first_agent_id, second_agent_id)
    apply_second_idempotency = apply_second_payload.get("idempotency")
    if not isinstance(apply_second_idempotency, dict) or not bool(apply_second_idempotency.get("replayed", False)):
        add_mismatch(
            "apply.idempotency.replayed",
            True,
            (
                apply_second_idempotency.get("replayed")
                if isinstance(apply_second_idempotency, dict)
                else apply_second_idempotency
            ),
        )

    plan_after_apply_count, plan_after_apply_error = _agents_count(client, token, plan_user_id)
    if plan_after_apply_count is None:
        add_mismatch("apply.after_count", "int", plan_after_apply_error)
    elif plan_before_count is not None and plan_after_apply_count != plan_before_count + 1:
        add_mismatch("apply.created_agents_delta", 1, plan_after_apply_count - plan_before_count)

    chat_before_count, chat_before_error = _agents_count(client, token, chat_user_id)
    if chat_before_count is None:
        add_mismatch("chat.before_count", "int", chat_before_error)

    chat_body = {
        "user_id": chat_user_id,
        "session_id": session_id,
        "messages": [{"role": "user", "content": request}],
        "stream": False,
    }
    chat_first = client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json=chat_body,
    )
    if chat_first.status_code != 200:
        add_mismatch("chat.first_status_code", 200, int(chat_first.status_code))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    chat_first_payload = chat_first.json() if chat_first.headers.get("content-type", "").startswith("application/json") else {}
    if not isinstance(chat_first_payload, dict):
        add_mismatch("chat.first_payload", "dict", str(type(chat_first_payload)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    quick_action = chat_first_payload.get("quick_action")
    if not isinstance(quick_action, dict):
        add_mismatch("chat.quick_action", "dict", str(type(quick_action)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    if _normalize_text(quick_action.get("type")) != "agent_created":
        add_mismatch("chat.quick_action.type", "agent_created", quick_action.get("type"))
    chat_first_spec = quick_action.get("quickstart_spec")
    if not isinstance(chat_first_spec, dict):
        add_mismatch("chat.quick_action.quickstart_spec", "dict", str(type(chat_first_spec)))
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": mismatches,
        }
    if not _ensure_reason_view(chat_first_spec.get("inference_reason_view")):
        add_mismatch("chat.quickstart_spec.inference_reason_view", "dict with version", chat_first_spec.get("inference_reason_view"))

    chat_first_agent = quick_action.get("agent")
    if not isinstance(chat_first_agent, dict) or not _normalize_text(chat_first_agent.get("id")):
        add_mismatch("chat.first_agent.id", "non-empty", chat_first_agent)

    chat_second = client.post(
        "/v1/chat/completions",
        headers=_auth(token),
        json=chat_body,
    )
    if chat_second.status_code != 200:
        add_mismatch("chat.second_status_code", 200, int(chat_second.status_code))
    else:
        chat_second_payload = (
            chat_second.json() if chat_second.headers.get("content-type", "").startswith("application/json") else {}
        )
        if not isinstance(chat_second_payload, dict):
            add_mismatch("chat.second_payload", "dict", str(type(chat_second_payload)))
        else:
            chat_second_action = chat_second_payload.get("quick_action")
            if not isinstance(chat_second_action, dict):
                add_mismatch("chat.second_quick_action", "dict", str(type(chat_second_action)))
            else:
                second_agent = chat_second_action.get("agent")
                if isinstance(chat_first_agent, dict) and isinstance(second_agent, dict):
                    first_agent_id = _normalize_text(chat_first_agent.get("id"))
                    second_agent_id = _normalize_text(second_agent.get("id"))
                    if first_agent_id != second_agent_id:
                        add_mismatch("chat.idempotency.agent_id", first_agent_id, second_agent_id)
                second_idempotency = chat_second_action.get("idempotency")
                if not isinstance(second_idempotency, dict) or not bool(second_idempotency.get("replayed", False)):
                    add_mismatch(
                        "chat.idempotency.replayed",
                        True,
                        (
                            second_idempotency.get("replayed")
                            if isinstance(second_idempotency, dict)
                            else second_idempotency
                        ),
                    )

    chat_after_count, chat_after_error = _agents_count(client, token, chat_user_id)
    if chat_after_count is None:
        add_mismatch("chat.after_count", "int", chat_after_error)
    elif chat_before_count is not None and chat_after_count != chat_before_count + 1:
        add_mismatch("chat.created_agents_delta", 1, chat_after_count - chat_before_count)

    plan_canonical = _canonical_from_plan(quickstart_plan)
    apply_canonical = _canonical_from_spec(apply_spec)
    chat_canonical = _canonical_from_spec(chat_first_spec)
    if apply_canonical != plan_canonical:
        add_mismatch("parity.plan_vs_apply", plan_canonical, apply_canonical)
    if chat_canonical != plan_canonical:
        add_mismatch("parity.plan_vs_chat", plan_canonical, chat_canonical)

    expected_kind = _normalize_text(expected.get("kind"))
    if expected_kind and plan_canonical.get("kind") != expected_kind:
        add_mismatch("expected.kind", expected_kind, plan_canonical.get("kind"))

    expected_source_policy_mode = _normalize_text(expected.get("source_policy_mode"))
    actual_source_policy_mode = str((plan_canonical.get("source_policy") or {}).get("mode") or "")
    if expected_source_policy_mode and actual_source_policy_mode != expected_source_policy_mode:
        add_mismatch("expected.source_policy_mode", expected_source_policy_mode, actual_source_policy_mode)

    expected_schedule_type = _normalize_text(expected.get("schedule_type"))
    actual_schedule_type = str((plan_canonical.get("automation") or {}).get("schedule_type") or "")
    if expected_schedule_type != "":
        if actual_schedule_type != expected_schedule_type:
            add_mismatch("expected.schedule_type", expected_schedule_type, actual_schedule_type)
    elif bool(expected.get("schedule_type") == ""):
        if actual_schedule_type != "":
            add_mismatch("expected.schedule_type", "", actual_schedule_type)

    return {
        "id": case_id,
        "status": "pass" if not mismatches else "fail",
        "mismatches": mismatches,
        "observed": {
            "plan": plan_canonical,
            "apply": apply_canonical,
            "chat": chat_canonical,
        },
    }


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    fixture_path = _resolve_path(repo_root, args.fixture)
    min_pass_rate = max(0.0, min(float(args.min_pass_rate), 1.0))
    if not fixture_path.exists():
        print(f"[agent-factory-quickstart-flow-gate] FAILED missing_fixture={fixture_path}")
        return 2

    try:
        fixture_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[agent-factory-quickstart-flow-gate] FAILED fixture_read_error={exc}")
        return 2

    raw_cases = fixture_payload.get("cases", []) if isinstance(fixture_payload, dict) else []
    if not isinstance(raw_cases, list) or not raw_cases:
        print("[agent-factory-quickstart-flow-gate] FAILED empty_or_invalid_cases")
        return 2

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[agent-factory-quickstart-flow-gate] FAILED import_error={exc}")
        return 2

    with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-quickstart-flow-gate-") as tmp:
        support_dir = Path(tmp) / "support"
        token = "quickstart-flow-admin-token"
        auth_tokens = {
            token: {"user_id": "quickstart-flow-admin", "scopes": ["admin", "user"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"
        os.environ["AMARYLLIS_AUTOMATION_ENABLED"] = "true"
        os.environ["AMARYLLIS_BACKUP_ENABLED"] = "false"
        os.environ["AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED"] = "false"
        os.environ["AMARYLLIS_REQUEST_TRACE_LOGS_ENABLED"] = "false"

        try:
            import runtime.server as server_module  # noqa: PLC0415

            server_module = importlib.reload(server_module)
            app = server_module.create_app()
        except Exception as exc:
            print(f"[agent-factory-quickstart-flow-gate] FAILED runtime_boot_error={exc}")
            return 2

        case_results: list[dict[str, Any]] = []
        try:
            with TestClient(app) as client:
                for index, raw_case in enumerate(raw_cases):
                    if not isinstance(raw_case, dict):
                        case_results.append(
                            {
                                "id": f"case-{index + 1}",
                                "status": "fail",
                                "mismatches": [
                                    {
                                        "field": "case",
                                        "expected": "dict",
                                        "actual": str(type(raw_case)),
                                    }
                                ],
                            }
                        )
                        continue
                    case_results.append(
                        _evaluate_case(
                            client=client,
                            token=token,
                            case_index=index,
                            raw_case=raw_case,
                        )
                    )
        finally:
            _shutdown_app(app)

    total = len(case_results)
    failed_cases = [item for item in case_results if str(item.get("status")) != "pass"]
    passed = total - len(failed_cases)
    pass_rate = (float(passed) / float(total)) if total > 0 else 0.0
    status = "pass" if pass_rate >= min_pass_rate and not failed_cases else "fail"

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "agent_factory_quickstart_flow_gate_v1",
        "fixture": str(fixture_path),
        "summary": {
            "status": status,
            "cases_total": total,
            "cases_passed": passed,
            "cases_failed": len(failed_cases),
            "pass_rate": round(pass_rate, 4),
            "min_pass_rate": round(min_pass_rate, 4),
        },
        "cases": case_results,
    }

    output_raw = _normalize_text(args.output)
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if status == "pass":
        print(
            "[agent-factory-quickstart-flow-gate] OK "
            f"cases={total} passed={passed} pass_rate={pass_rate:.3f}"
        )
        return 0

    failed_ids = ",".join(_normalize_text(item.get("id"), default="case") or "case" for item in failed_cases[:20])
    print(
        "[agent-factory-quickstart-flow-gate] FAILED "
        f"cases={total} passed={passed} pass_rate={pass_rate:.3f} "
        f"failed_cases={failed_ids}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
