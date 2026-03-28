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
            "Validate supervisor mission contract (docs + manager checkpoint/resume + "
            "runtime API for /supervisor/graphs/* and objective verification)."
        )
    )
    parser.add_argument(
        "--supervisor-doc",
        default="docs/supervisor-task-graph-contract.md",
        help="Path to supervisor task-graph contract doc.",
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


def _node_by_id(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    nodes = graph.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if isinstance(node, dict) and str(node.get("node_id") or "") == node_id:
                return node
    raise ValueError(f"node not found: {node_id}")


class _FakeAgentManager:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._run_seq = 0

    def create_run(
        self,
        *,
        agent_id: str,
        user_message: str,
        user_id: str,
        session_id: str | None,
        max_attempts: int | None = None,
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._run_seq += 1
        run_id = f"run-{self._run_seq}"
        run = {
            "id": run_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "session_id": session_id,
            "message": user_message,
            "status": "queued",
            "max_attempts": max_attempts,
            "budget": budget,
            "result": {},
        }
        self._runs[run_id] = dict(run)
        return dict(run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return dict(run)

    def set_run_status(self, run_id: str, status: str, *, error: str | None = None) -> None:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        run["status"] = status
        if error is not None:
            run["result"] = {"error": error}

    def set_run_response(self, run_id: str, response: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        result = run.get("result")
        if not isinstance(result, dict):
            result = {}
            run["result"] = result
        result["response"] = str(response)


def _run_manager_contract_checks(
    *,
    add_check: Any,
) -> None:
    try:
        from storage.database import Database  # noqa: PLC0415
        from supervisor.task_graph_manager import SupervisorTaskGraphManager  # noqa: PLC0415

        fake_agent_manager = _FakeAgentManager()
        with tempfile.TemporaryDirectory(prefix="amaryllis-supervisor-mission-gate-manager-") as tmp_dir:
            db_path = Path(tmp_dir) / "amaryllis.db"
            database = Database(db_path)
            manager = SupervisorTaskGraphManager(
                agent_manager=fake_agent_manager,
                database=database,
            )

            created = manager.create_graph(
                user_id="sup-user",
                objective="Resolve production incident",
                nodes=[
                    {
                        "node_id": "triage",
                        "agent_id": "agent-triage",
                        "message": "Run triage",
                    },
                    {
                        "node_id": "fix",
                        "agent_id": "agent-fix",
                        "message": "Apply fix",
                        "depends_on": ["triage"],
                    },
                ],
            )
            graph_id = str(created.get("id") or "")
            add_check("manager_create_graph_id", graph_id.startswith("sup-"), f"graph_id={graph_id}")

            launched = manager.launch_graph(
                graph_id=graph_id,
                user_id="sup-user",
                session_id="sup-mission-gate-session",
            )
            triage_run_id = str(_node_by_id(launched, "triage").get("run_id") or "")
            add_check("manager_launch_triage_run", bool(triage_run_id), f"triage_run_id={triage_run_id}")

            fake_agent_manager.set_run_response(triage_run_id, "Root cause identified and mitigation started.")
            fake_agent_manager.set_run_status(triage_run_id, "succeeded")
            after_triage = manager.tick_graph(graph_id=graph_id, user_id="sup-user")
            fix_run_id = str(_node_by_id(after_triage, "fix").get("run_id") or "")
            add_check("manager_tick_fix_scheduled", bool(fix_run_id), f"fix_run_id={fix_run_id}")

            recovered_manager = SupervisorTaskGraphManager(
                agent_manager=fake_agent_manager,
                database=database,
            )
            recovered_graph = recovered_manager.get_graph(graph_id=graph_id)
            recovered_fix_run_id = str(_node_by_id(recovered_graph, "fix").get("run_id") or "")
            add_check(
                "manager_checkpoint_resume_preserves_run",
                recovered_fix_run_id == fix_run_id,
                f"recovered_fix_run_id={recovered_fix_run_id}",
            )

            fake_agent_manager.set_run_response(fix_run_id, "Fix deployed and validated successfully.")
            fake_agent_manager.set_run_status(fix_run_id, "succeeded")
            completed = recovered_manager.tick_graph(graph_id=graph_id, user_id="sup-user")
            add_check(
                "manager_checkpoint_resume_continues_to_success",
                str(completed.get("status") or "") == "succeeded",
                f"status={completed.get('status')}",
            )

            manual_graph = recovered_manager.create_graph(
                user_id="sup-user",
                objective="Manual approval objective",
                nodes=[
                    {
                        "node_id": "report",
                        "agent_id": "agent-report",
                        "message": "Prepare report",
                    }
                ],
                objective_verification={"mode": "manual"},
            )
            manual_graph_id = str(manual_graph.get("id") or "")
            launched_manual = recovered_manager.launch_graph(graph_id=manual_graph_id, user_id="sup-user")
            manual_run_id = str(_node_by_id(launched_manual, "report").get("run_id") or "")
            fake_agent_manager.set_run_response(manual_run_id, "Report drafted with full objective evidence.")
            fake_agent_manager.set_run_status(manual_run_id, "succeeded")
            review_required = recovered_manager.tick_graph(graph_id=manual_graph_id, user_id="sup-user")
            add_check(
                "manager_manual_mode_review_required",
                str(review_required.get("status") or "") == "review_required",
                f"status={review_required.get('status')}",
            )
            verified = recovered_manager.verify_graph_objective(
                graph_id=manual_graph_id,
                user_id="sup-user",
                override_pass=True,
                note="Approved by operator",
            )
            add_check(
                "manager_manual_mode_verify_override",
                str(verified.get("status") or "") == "succeeded"
                and str((verified.get("objective_verification") or {}).get("status") or "") == "passed",
                f"status={verified.get('status')}, verification={(verified.get('objective_verification') or {}).get('status')}",
            )

            failed_graph = recovered_manager.create_graph(
                user_id="sup-user",
                objective="Keyword verification objective",
                nodes=[
                    {
                        "node_id": "analysis",
                        "agent_id": "agent-analysis",
                        "message": "Analyze issue",
                    }
                ],
                objective_verification={
                    "mode": "auto",
                    "required_keywords": ["nonexistent-keyword"],
                    "on_failure": "failed",
                    "min_response_chars": 5,
                },
            )
            failed_graph_id = str(failed_graph.get("id") or "")
            launched_failed = recovered_manager.launch_graph(graph_id=failed_graph_id, user_id="sup-user")
            failed_run_id = str(_node_by_id(launched_failed, "analysis").get("run_id") or "")
            fake_agent_manager.set_run_response(failed_run_id, "Analysis complete with details.")
            fake_agent_manager.set_run_status(failed_run_id, "succeeded")
            failed_graph_after_tick = recovered_manager.tick_graph(graph_id=failed_graph_id, user_id="sup-user")
            failed_verification = (failed_graph_after_tick.get("objective_verification") or {})
            add_check(
                "manager_objective_keyword_failure",
                str(failed_graph_after_tick.get("status") or "") == "failed"
                and str(failed_verification.get("status") or "") == "failed",
                f"status={failed_graph_after_tick.get('status')}, verification={failed_verification.get('status')}",
            )

            persisted = database.get_supervisor_graph(graph_id)
            add_check(
                "manager_checkpoint_store_persisted",
                isinstance(persisted, dict)
                and str(persisted.get("id") or "") == graph_id
                and int(persisted.get("checkpoint_count") or 0) > 0,
                f"checkpoint_count={(persisted or {}).get('checkpoint_count')}",
            )
            database.close()
    except Exception as exc:  # pragma: no cover - fallback diagnostics for CI
        add_check("manager_gate_execution", False, f"{type(exc).__name__}: {exc}")


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    supervisor_doc = _resolve_path(repo_root, str(args.supervisor_doc))
    token = str(args.token).strip() or "dev-token"

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if supervisor_doc.exists():
        text = supervisor_doc.read_text(encoding="utf-8")
        add_check("supervisor_doc_exists", True, str(supervisor_doc))
        add_check(
            "supervisor_doc_endpoints",
            all(
                endpoint in text
                for endpoint in (
                    "GET /supervisor/graphs/contract",
                    "POST /supervisor/graphs/create",
                    "GET /supervisor/graphs",
                    "GET /supervisor/graphs/{graph_id}",
                    "POST /supervisor/graphs/{graph_id}/launch",
                    "POST /supervisor/graphs/{graph_id}/tick",
                    "POST /supervisor/graphs/{graph_id}/verify",
                )
            ),
            "all supervisor endpoints documented",
        )
        add_check(
            "supervisor_doc_checkpoint_resume",
            "Checkpoint + Resume" in text and "supervisor_graphs" in text,
            "checkpoint/resume contract documented",
        )
        add_check(
            "supervisor_doc_objective_verification",
            "Objective Verification Gates" in text and "objective_verification" in text,
            "objective verification policy documented",
        )
    else:
        add_check("supervisor_doc_exists", False, f"missing: {supervisor_doc}")

    _run_manager_contract_checks(add_check=add_check)

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-supervisor-mission-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    app = None

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            token: {"user_id": "sup-user", "scopes": ["user"]},
            "sup-admin-token": {"user_id": "sup-admin", "scopes": ["admin", "user"]},
            "sup-other-token": {"user_id": "sup-other", "scopes": ["user"]},
            "sup-service-token": {"user_id": "sup-service", "scopes": ["service"]},
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

    restart_graph_id = ""

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from runtime.server import create_app  # noqa: PLC0415

        app = create_app()
        with TestClient(app) as client:
            contract_resp = client.get("/supervisor/graphs/contract", headers=_auth(token))
            add_check(
                "runtime_supervisor_contract_ok",
                contract_resp.status_code == 200,
                f"status={contract_resp.status_code}",
            )
            contract_payload = contract_resp.json() if _is_json_response(dict(contract_resp.headers)) else {}
            graph_statuses = set(str(item) for item in (contract_payload.get("graph_statuses") or []))
            node_statuses = set(str(item) for item in (contract_payload.get("node_statuses") or []))
            objective_contract = contract_payload.get("objective_verification") or {}
            checkpoint_contract = contract_payload.get("checkpoint_resume") or {}
            add_check(
                "runtime_supervisor_contract_statuses",
                {"planned", "running", "review_required", "succeeded", "failed"}.issubset(graph_statuses)
                and {"planned", "queued", "running", "succeeded", "failed", "blocked"}.issubset(node_statuses),
                f"graph_statuses={sorted(graph_statuses)}",
            )
            add_check(
                "runtime_supervisor_contract_verify_endpoint",
                str(objective_contract.get("verify_endpoint") or "") == "/supervisor/graphs/{graph_id}/verify",
                f"verify_endpoint={objective_contract.get('verify_endpoint')}",
            )
            add_check(
                "runtime_supervisor_contract_checkpoint_resume",
                bool(checkpoint_contract.get("enabled"))
                and str(checkpoint_contract.get("store") or "") == "sqlite.supervisor_graphs",
                f"checkpoint_resume={checkpoint_contract}",
            )

            create_triage_agent = client.post(
                "/agents/create",
                headers=_auth(token),
                json={
                    "name": "Supervisor Gate Triage",
                    "system_prompt": "supervisor-mission-gate",
                    "user_id": "sup-user",
                    "tools": ["web_search"],
                },
            )
            add_check(
                "runtime_supervisor_create_triage_agent_ok",
                create_triage_agent.status_code == 200,
                f"status={create_triage_agent.status_code}",
            )
            triage_agent_id = str((create_triage_agent.json() if _is_json_response(dict(create_triage_agent.headers)) else {}).get("id") or "")
            add_check("runtime_supervisor_triage_agent_id", bool(triage_agent_id), f"agent_id={triage_agent_id}")

            create_fix_agent = client.post(
                "/agents/create",
                headers=_auth(token),
                json={
                    "name": "Supervisor Gate Fix",
                    "system_prompt": "supervisor-mission-gate",
                    "user_id": "sup-user",
                    "tools": ["web_search"],
                },
            )
            add_check(
                "runtime_supervisor_create_fix_agent_ok",
                create_fix_agent.status_code == 200,
                f"status={create_fix_agent.status_code}",
            )
            fix_agent_id = str((create_fix_agent.json() if _is_json_response(dict(create_fix_agent.headers)) else {}).get("id") or "")
            add_check("runtime_supervisor_fix_agent_id", bool(fix_agent_id), f"agent_id={fix_agent_id}")

            create_graph_resp = client.post(
                "/supervisor/graphs/create",
                headers=_auth(token),
                json={
                    "user_id": "sup-user",
                    "objective": "Incident response mission",
                    "objective_verification": {
                        "mode": "manual",
                    },
                    "nodes": [
                        {
                            "node_id": "triage",
                            "agent_id": triage_agent_id,
                            "message": "Run triage",
                        },
                        {
                            "node_id": "fix",
                            "agent_id": fix_agent_id,
                            "message": "Run fix",
                            "depends_on": ["triage"],
                        },
                    ],
                },
            )
            add_check(
                "runtime_supervisor_create_graph_ok",
                create_graph_resp.status_code == 200,
                f"status={create_graph_resp.status_code}",
            )
            create_graph_payload = (
                create_graph_resp.json() if _is_json_response(dict(create_graph_resp.headers)) else {}
            )
            supervisor_graph = create_graph_payload.get("supervisor_graph") if isinstance(create_graph_payload, dict) else {}
            graph_id = str((supervisor_graph or {}).get("id") or "")
            add_check("runtime_supervisor_create_graph_id", graph_id.startswith("sup-"), f"graph_id={graph_id}")

            launch_resp = client.post(
                f"/supervisor/graphs/{graph_id}/launch",
                headers=_auth(token),
                json={"session_id": "supervisor-mission-gate-session"},
            )
            add_check("runtime_supervisor_launch_ok", launch_resp.status_code == 200, f"status={launch_resp.status_code}")
            launch_payload = launch_resp.json() if _is_json_response(dict(launch_resp.headers)) else {}
            launch_graph = launch_payload.get("supervisor_graph") if isinstance(launch_payload, dict) else {}
            add_check(
                "runtime_supervisor_launch_status",
                str((launch_graph or {}).get("status") or "") in {"running", "review_required", "succeeded"},
                f"status={(launch_graph or {}).get('status')}",
            )

            tick_resp = client.post(
                f"/supervisor/graphs/{graph_id}/tick",
                headers=_auth(token),
                json={"noop": True},
            )
            add_check("runtime_supervisor_tick_ok", tick_resp.status_code == 200, f"status={tick_resp.status_code}")
            tick_payload = tick_resp.json() if _is_json_response(dict(tick_resp.headers)) else {}
            tick_graph = tick_payload.get("supervisor_graph") if isinstance(tick_payload, dict) else {}
            tick_status = str((tick_graph or {}).get("status") or "")
            add_check(
                "runtime_supervisor_tick_status",
                tick_status in {"running", "review_required", "succeeded", "failed"},
                f"status={tick_status}",
            )

            list_resp = client.get("/supervisor/graphs", headers=_auth(token), params={"user_id": "sup-user", "limit": 20})
            add_check("runtime_supervisor_list_ok", list_resp.status_code == 200, f"status={list_resp.status_code}")
            list_payload = list_resp.json() if _is_json_response(dict(list_resp.headers)) else {}
            list_items = list_payload.get("items") if isinstance(list_payload, dict) else []
            add_check(
                "runtime_supervisor_list_contains_graph",
                isinstance(list_items, list)
                and any(isinstance(item, dict) and str(item.get("id") or "") == graph_id for item in list_items),
                f"count={len(list_items) if isinstance(list_items, list) else 'n/a'}",
            )

            verify_resp = client.post(
                f"/supervisor/graphs/{graph_id}/verify",
                headers=_auth(token),
                json={
                    "override_pass": True,
                    "note": "approved by supervisor mission gate",
                },
            )
            add_check("runtime_supervisor_verify_ok", verify_resp.status_code == 200, f"status={verify_resp.status_code}")
            verify_payload = verify_resp.json() if _is_json_response(dict(verify_resp.headers)) else {}
            verify_graph = verify_payload.get("supervisor_graph") if isinstance(verify_payload, dict) else {}
            verification = (verify_graph or {}).get("objective_verification") if isinstance(verify_graph, dict) else {}
            add_check(
                "runtime_supervisor_verify_status_passed",
                str((verification or {}).get("status") or "") == "passed",
                f"verification_status={(verification or {}).get('status')}",
            )

            foreign_get = client.get(f"/supervisor/graphs/{graph_id}", headers=_auth("sup-other-token"))
            add_check(
                "runtime_supervisor_owner_guard",
                foreign_get.status_code == 403,
                f"status={foreign_get.status_code}",
            )
            foreign_payload = foreign_get.json() if _is_json_response(dict(foreign_get.headers)) else {}
            add_check(
                "runtime_supervisor_owner_guard_error_type",
                str((foreign_payload.get("error") or {}).get("type") or "") == "permission_denied",
                f"error_type={(foreign_payload.get('error') or {}).get('type')}",
            )

            restart_create_resp = client.post(
                "/supervisor/graphs/create",
                headers=_auth(token),
                json={
                    "user_id": "sup-user",
                    "objective": "Restart persistence mission",
                    "nodes": [
                        {
                            "node_id": "single",
                            "agent_id": triage_agent_id,
                            "message": "Run single step",
                        }
                    ],
                },
            )
            add_check(
                "runtime_supervisor_restart_graph_create_ok",
                restart_create_resp.status_code == 200,
                f"status={restart_create_resp.status_code}",
            )
            restart_payload = restart_create_resp.json() if _is_json_response(dict(restart_create_resp.headers)) else {}
            restart_graph = restart_payload.get("supervisor_graph") if isinstance(restart_payload, dict) else {}
            restart_graph_id = str((restart_graph or {}).get("id") or "")
            add_check(
                "runtime_supervisor_restart_graph_id",
                restart_graph_id.startswith("sup-"),
                f"graph_id={restart_graph_id}",
            )

            restart_launch = client.post(
                f"/supervisor/graphs/{restart_graph_id}/launch",
                headers=_auth(token),
                json={"session_id": "supervisor-restart-session"},
            )
            add_check(
                "runtime_supervisor_restart_graph_launch_ok",
                restart_launch.status_code == 200,
                f"status={restart_launch.status_code}",
            )

        _shutdown_app(app)
        app = None

        app = create_app()
        with TestClient(app) as client:
            recovered_resp = client.get(f"/supervisor/graphs/{restart_graph_id}", headers=_auth(token))
            add_check(
                "runtime_supervisor_restart_recovery_get_ok",
                recovered_resp.status_code == 200,
                f"status={recovered_resp.status_code}",
            )
            recovered_payload = recovered_resp.json() if _is_json_response(dict(recovered_resp.headers)) else {}
            recovered_graph = recovered_payload.get("supervisor_graph") if isinstance(recovered_payload, dict) else {}
            recovered_id = str((recovered_graph or {}).get("id") or "")
            recovered_nodes = (recovered_graph or {}).get("nodes") if isinstance(recovered_graph, dict) else []
            add_check(
                "runtime_supervisor_restart_recovery_graph_id",
                recovered_id == restart_graph_id,
                f"recovered_graph_id={recovered_id}",
            )
            add_check(
                "runtime_supervisor_restart_recovery_nodes",
                isinstance(recovered_nodes, list) and len(recovered_nodes) >= 1,
                f"node_count={len(recovered_nodes) if isinstance(recovered_nodes, list) else 'n/a'}",
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
        "suite": "supervisor_mission_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "supervisor_doc": str(supervisor_doc),
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if failed:
        print("[supervisor-mission-gate] FAILED")
        for item in failed:
            print(f"- {item.get('name')}: {item.get('detail')}")
        return 1

    print(f"[supervisor-mission-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
