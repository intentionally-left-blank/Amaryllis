from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError

router = APIRouter(tags=["agents"])
RUN_STATUSES: set[str] = {"queued", "running", "succeeded", "failed", "canceled"}
REPLAY_TIMELINE_PRESETS: set[str] = {"errors", "tools", "verify"}
RUN_INTERACTION_MODES: set[str] = {"plan", "execute"}


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sign_action(
    request: Request,
    *,
    action: str,
    payload: dict[str, Any],
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    status: str = "succeeded",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.security_manager.signed_action(
            action=action,
            payload=payload,
            request_id=_request_id(request),
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


def _normalize_run_interaction_mode(raw_mode: str | None) -> str:
    normalized = str(raw_mode or "execute").strip().lower() or "execute"
    if normalized not in RUN_INTERACTION_MODES:
        allowed = ", ".join(sorted(RUN_INTERACTION_MODES))
        raise ValidationError(
            f"Invalid interaction_mode '{raw_mode}'. Allowed values: {allowed}."
        )
    return normalized


def _run_mode_trust_boundary(mode: str) -> dict[str, Any]:
    if mode == "plan":
        return {
            "mode": "plan",
            "execution_performed": False,
            "tool_execution_performed": False,
            "requires_explicit_execute_call": True,
            "summary": (
                "Planning mode is dry-run only. It does not create runs or execute tools. "
                "A separate execute request is required."
            ),
        }
    return {
        "mode": "execute",
        "execution_performed": True,
        "tool_execution_performed": True,
        "requires_explicit_execute_call": False,
        "summary": "Execute mode creates an async run immediately under mission budgets and policy guardrails.",
    }


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    user_id: str | None = None


class AgentChatRequest(BaseModel):
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_id: str | None = None


class RunBudgetRequest(BaseModel):
    max_tokens: int | None = Field(default=None, ge=256, le=2_000_000)
    max_duration_sec: float | None = Field(default=None, ge=10.0, le=86_400.0)
    max_tool_calls: int | None = Field(default=None, ge=1, le=200)
    max_tool_errors: int | None = Field(default=None, ge=0, le=200)


class AgentRunCreateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_id: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    budget: RunBudgetRequest | None = None


class AgentRunSimulationRequest(BaseModel):
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_id: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    budget: RunBudgetRequest | None = None


class AgentRunDispatchRequest(BaseModel):
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_id: str | None = None
    interaction_mode: str = Field(default="execute")
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    budget: RunBudgetRequest | None = None


@router.post("/agents/create")
def create_agent(payload: CreateAgentRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = services.agent_manager.create_agent(
            name=payload.name,
            system_prompt=payload.system_prompt,
            model=payload.model,
            tools=payload.tools,
            user_id=effective_user_id,
        )
        receipt = _sign_action(
            request,
            action="agent_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent",
            target_id=agent.id,
        )
        payload_out = agent.to_record()
        payload_out["action_receipt"] = receipt
        payload_out["request_id"] = _request_id(request)
        return payload_out
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent",
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="agent_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent",
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/agents")
def list_agents(request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    agents = services.agent_manager.list_agents(user_id=effective_user_id)
    return {
        "items": [agent.to_record() for agent in agents],
        "count": len(agents),
        "request_id": _request_id(request),
    }


@router.post("/agents/{agent_id}/runs")
def create_agent_run(
    payload: AgentRunCreateRequest,
    request: Request,
    agent_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = services.agent_manager.get_agent(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        assert_owner(
            owner_user_id=agent.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=agent_id,
        )
        run = services.agent_manager.create_run(
            agent_id=agent_id,
            user_message=payload.message,
            user_id=effective_user_id,
            session_id=payload.session_id,
            max_attempts=payload.max_attempts,
            budget=payload.budget.model_dump(exclude_none=True) if payload.budget is not None else None,
        )
        receipt = _sign_action(
            request,
            action="agent_run_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent_run",
            target_id=str(run.get("id")),
            details={"agent_id": agent_id},
        )
        return {
            "run": run,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_run_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent_run",
            details={"agent_id": agent_id, "error": str(exc)},
            status="failed",
        )
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="agent_run_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent_run",
            details={"agent_id": agent_id, "error": str(exc)},
            status="failed",
        )
        raise ProviderError(str(exc)) from exc


@router.post("/agents/{agent_id}/runs/simulate")
def simulate_agent_run(
    payload: AgentRunSimulationRequest,
    request: Request,
    agent_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = services.agent_manager.get_agent(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        assert_owner(
            owner_user_id=agent.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=agent_id,
        )
        simulation = services.agent_manager.simulate_run(
            agent_id=agent_id,
            user_message=payload.message,
            user_id=effective_user_id,
            session_id=payload.session_id,
            max_attempts=payload.max_attempts,
            budget=payload.budget.model_dump(exclude_none=True) if payload.budget is not None else None,
        )
        dry_run_receipt = _sign_action(
            request,
            action="agent_run_simulate",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent_run_simulation",
            target_id=str(simulation.get("simulation_id") or ""),
            details={"agent_id": agent_id, "mode": "dry_run"},
        )
        return {
            "simulation": simulation,
            "dry_run_receipt": dry_run_receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_run_simulate",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent_run_simulation",
            details={"agent_id": agent_id, "error": str(exc)},
            status="failed",
        )
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="agent_run_simulate",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent_run_simulation",
            details={"agent_id": agent_id, "error": str(exc)},
            status="failed",
        )
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/interaction-modes")
def get_agent_run_interaction_modes(request: Request) -> dict[str, Any]:
    _ = auth_context_from_request(request)
    return {
        "modes": [
            {
                "mode": "plan",
                "summary": "Dry-run planning preview (no run is created).",
                "endpoint": "/agents/{agent_id}/runs/dispatch",
                "execution_performed": False,
            },
            {
                "mode": "execute",
                "summary": "Immediate async run creation and execution.",
                "endpoint": "/agents/{agent_id}/runs/dispatch",
                "execution_performed": True,
            },
        ],
        "supported_interaction_modes": sorted(RUN_INTERACTION_MODES),
        "request_id": _request_id(request),
    }


@router.post("/agents/{agent_id}/runs/dispatch")
def dispatch_agent_run(
    payload: AgentRunDispatchRequest,
    request: Request,
    agent_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    interaction_mode = _normalize_run_interaction_mode(payload.interaction_mode)
    budget_payload = payload.budget.model_dump(exclude_none=True) if payload.budget is not None else None
    action_payload = {
        **payload.model_dump(exclude_none=True),
        "user_id": effective_user_id,
        "interaction_mode": interaction_mode,
    }
    if budget_payload is not None:
        action_payload["budget"] = budget_payload

    try:
        agent = services.agent_manager.get_agent(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        assert_owner(
            owner_user_id=agent.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=agent_id,
        )

        if interaction_mode == "plan":
            simulation = services.agent_manager.simulate_run(
                agent_id=agent_id,
                user_message=payload.message,
                user_id=effective_user_id,
                session_id=payload.session_id,
                max_attempts=payload.max_attempts,
                budget=budget_payload,
            )
            dry_run_receipt = _sign_action(
                request,
                action="agent_run_dispatch",
                payload=action_payload,
                actor=auth.user_id,
                target_type="agent_run_simulation",
                target_id=str(simulation.get("simulation_id") or ""),
                details={
                    "agent_id": agent_id,
                    "interaction_mode": interaction_mode,
                    "execution_performed": False,
                },
            )
            execute_payload: dict[str, Any] = {
                "user_id": effective_user_id,
                "message": payload.message,
                "interaction_mode": "execute",
            }
            if payload.session_id is not None:
                execute_payload["session_id"] = payload.session_id
            if payload.max_attempts is not None:
                execute_payload["max_attempts"] = payload.max_attempts
            if budget_payload is not None:
                execute_payload["budget"] = budget_payload
            return {
                "interaction_mode": interaction_mode,
                "trust_boundary": _run_mode_trust_boundary(interaction_mode),
                "simulation": simulation,
                "dry_run_receipt": dry_run_receipt,
                "execute_hint": {
                    "endpoint": f"/agents/{agent_id}/runs/dispatch",
                    "payload": execute_payload,
                },
                "supported_interaction_modes": sorted(RUN_INTERACTION_MODES),
                "request_id": _request_id(request),
            }

        run = services.agent_manager.create_run(
            agent_id=agent_id,
            user_message=payload.message,
            user_id=effective_user_id,
            session_id=payload.session_id,
            max_attempts=payload.max_attempts,
            budget=budget_payload,
        )
        receipt = _sign_action(
            request,
            action="agent_run_dispatch",
            payload=action_payload,
            actor=auth.user_id,
            target_type="agent_run",
            target_id=str(run.get("id")),
            details={
                "agent_id": agent_id,
                "interaction_mode": interaction_mode,
                "execution_performed": True,
            },
        )
        return {
            "interaction_mode": interaction_mode,
            "trust_boundary": _run_mode_trust_boundary(interaction_mode),
            "run": run,
            "action_receipt": receipt,
            "supported_interaction_modes": sorted(RUN_INTERACTION_MODES),
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_run_dispatch",
            payload=action_payload,
            actor=auth.user_id,
            target_type="agent_run",
            details={
                "agent_id": agent_id,
                "interaction_mode": interaction_mode,
                "error": str(exc),
            },
            status="failed",
        )
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="agent_run_dispatch",
            payload=action_payload,
            actor=auth.user_id,
            target_type="agent_run",
            details={
                "agent_id": agent_id,
                "interaction_mode": interaction_mode,
                "error": str(exc),
            },
            status="failed",
        )
        raise ProviderError(str(exc)) from exc


@router.get("/agents/{agent_id}/runs")
def list_agent_runs(
    request: Request,
    agent_id: str = Path(..., min_length=1),
    user_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    include_result: bool = Query(default=False),
    include_checkpoints: bool = Query(default=False),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        if status is not None:
            normalized_status = status.strip().lower()
            if normalized_status not in RUN_STATUSES:
                allowed = ", ".join(sorted(RUN_STATUSES))
                raise ValidationError(f"Invalid status: {status}. Allowed: {allowed}")
            status = normalized_status
        runs = services.agent_manager.list_runs(
            user_id=effective_user_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )
        if not include_result or not include_checkpoints:
            compact_runs: list[dict[str, Any]] = []
            for run in runs:
                row = dict(run)
                if not include_result:
                    row["result"] = None
                if not include_checkpoints:
                    row["checkpoints"] = []
                compact_runs.append(row)
            runs = compact_runs
        return {
            "items": runs,
            "count": len(runs),
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}")
def get_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        return {
            "run": run,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/replay")
def replay_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
    preset: str | None = Query(default=None),
    stage: list[str] | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    failure_class: list[str] | None = Query(default=None),
    retryable: bool | None = Query(default=None),
    attempt: int | None = Query(default=None, ge=1, le=100),
    timeline_limit: int = Query(default=0, ge=0, le=5000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    normalized_preset = str(preset or "").strip().lower() or None
    if normalized_preset and normalized_preset not in REPLAY_TIMELINE_PRESETS:
        allowed = ", ".join(sorted(REPLAY_TIMELINE_PRESETS))
        raise ValidationError(f"Invalid replay preset '{preset}'. Allowed values: {allowed}.")
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        replay = services.agent_manager.replay_run_filtered(
            run_id=run_id,
            preset=normalized_preset,
            stages=stage,
            statuses=status,
            failure_classes=failure_class,
            retryable=retryable,
            attempt=attempt,
            timeline_limit=timeline_limit,
        )
        return {
            "replay": replay,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/events")
def stream_agent_run_events(
    request: Request,
    run_id: str = Path(..., min_length=1),
    from_index: int = Query(default=0, ge=0, le=200_000),
    poll_interval_ms: int = Query(default=250, ge=50, le=2000),
    timeout_sec: float = Query(default=30.0, ge=1.0, le=300.0),
    include_snapshot: bool = Query(default=True),
    include_heartbeat: bool = Query(default=False),
) -> StreamingResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc

    def event_stream() -> Any:
        started_monotonic = time.monotonic()
        next_index = max(0, int(from_index))
        snapshot_emitted = False
        sleep_sec = max(0.05, float(poll_interval_ms) / 1000.0)

        while True:
            try:
                current_run = services.agent_manager.get_run(run_id=run_id)
            except Exception as exc:
                yield _sse_data(
                    {
                        "event": "error",
                        "run_id": run_id,
                        "message": str(exc),
                    }
                )
                yield "data: [DONE]\n\n"
                return

            raw_checkpoints = current_run.get("checkpoints")
            checkpoints = (
                [item for item in raw_checkpoints if isinstance(item, dict)]
                if isinstance(raw_checkpoints, list)
                else []
            )
            checkpoint_count = len(checkpoints)
            status = str(current_run.get("status") or "").strip().lower() or "unknown"

            if include_snapshot and not snapshot_emitted:
                snapshot_emitted = True
                yield _sse_data(
                    {
                        "event": "snapshot",
                        "run_id": run_id,
                        "status": status,
                        "attempts": int(current_run.get("attempts", 0)),
                        "max_attempts": int(current_run.get("max_attempts", 0)),
                        "checkpoint_count": checkpoint_count,
                        "next_index": next_index,
                    }
                )

            if next_index < checkpoint_count:
                for idx in range(next_index, checkpoint_count):
                    checkpoint = checkpoints[idx]
                    yield _sse_data(
                        {
                            "event": "checkpoint",
                            "run_id": run_id,
                            "status": status,
                            "index": idx + 1,
                            "checkpoint": checkpoint,
                        }
                    )
                next_index = checkpoint_count
            elif include_heartbeat:
                yield _sse_data(
                    {
                        "event": "heartbeat",
                        "run_id": run_id,
                        "status": status,
                        "checkpoint_count": checkpoint_count,
                        "next_index": next_index,
                    }
                )

            if status in {"succeeded", "failed", "canceled"}:
                yield _sse_data(
                    {
                        "event": "done",
                        "run_id": run_id,
                        "status": status,
                        "checkpoint_count": checkpoint_count,
                        "next_index": next_index,
                    }
                )
                yield "data: [DONE]\n\n"
                return

            if (time.monotonic() - started_monotonic) >= float(timeout_sec):
                yield _sse_data(
                    {
                        "event": "timeout",
                        "run_id": run_id,
                        "status": status,
                        "checkpoint_count": checkpoint_count,
                        "next_index": next_index,
                    }
                )
                yield "data: [DONE]\n\n"
                return

            time.sleep(sleep_sec)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/agents/runs/{run_id}/diagnostics")
def diagnose_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        diagnostics = services.agent_manager.diagnose_run(run_id=run_id)
        return {
            "diagnostics": diagnostics,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/diagnostics/package")
def diagnostics_package_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        package = services.agent_manager.build_run_diagnostics_package(run_id=run_id)
        return {
            "package": package,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/audit")
def audit_timeline_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
    include_tool_calls: bool = Query(default=True),
    include_security_actions: bool = Query(default=True),
    limit: int = Query(default=2000, ge=1, le=20000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        audit = services.agent_manager.build_run_audit_timeline(
            run_id=run_id,
            include_tool_calls=include_tool_calls,
            include_security_actions=include_security_actions,
            limit=limit,
        )
        return {
            "audit": audit,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/explain")
def explain_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
    include_tool_calls: bool = Query(default=True),
    include_security_actions: bool = Query(default=True),
    limit: int = Query(default=2000, ge=1, le=20000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        explainability = services.agent_manager.build_run_explainability_feed(
            run_id=run_id,
            include_tool_calls=include_tool_calls,
            include_security_actions=include_security_actions,
            limit=limit,
        )
        return {
            "explainability": explainability,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/audit/export", response_model=None)
def export_audit_timeline_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
    format: str = Query(default="json"),
    include_tool_calls: bool = Query(default=True),
    include_security_actions: bool = Query(default=True),
    limit: int = Query(default=2000, ge=1, le=20000),
) -> Any:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    normalized_format = str(format or "json").strip().lower() or "json"
    if normalized_format not in {"json", "csv"}:
        raise ValidationError("Unsupported export format. Allowed values: json, csv.")
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        exported = services.agent_manager.export_run_audit_timeline(
            run_id=run_id,
            export_format=normalized_format,
            include_tool_calls=include_tool_calls,
            include_security_actions=include_security_actions,
            limit=limit,
        )
        if str(exported.get("format") or "") == "csv":
            filename = str(exported.get("filename") or f"run-audit-{run_id}.csv")
            content = str(exported.get("content") or "")
            return PlainTextResponse(
                content=content,
                media_type=str(exported.get("content_type") or "text/csv; charset=utf-8"),
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        payload = exported.get("payload")
        return {
            "audit": payload if isinstance(payload, dict) else {},
            "export": {
                "format": str(exported.get("format") or "json"),
                "filename": str(exported.get("filename") or f"run-audit-{run_id}.json"),
                "content_type": str(exported.get("content_type") or "application/json"),
            },
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        if "unsupported export format" in str(exc).lower():
            raise ValidationError(str(exc)) from exc
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/issues")
def list_agent_run_issues(
    request: Request,
    run_id: str = Path(..., min_length=1),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        items = services.agent_manager.list_run_issues(run_id=run_id, limit=limit)
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/artifacts")
def list_agent_run_artifacts(
    request: Request,
    run_id: str = Path(..., min_length=1),
    issue_id: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(run.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        items = services.agent_manager.list_run_artifacts(
            run_id=run_id,
            issue_id=issue_id,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/agents/runs/{run_id}/cancel")
def cancel_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        existing = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        run = services.agent_manager.cancel_run(run_id=run_id)
        receipt = _sign_action(
            request,
            action="agent_run_cancel",
            payload={"run_id": run_id},
            actor=auth.user_id,
            target_type="agent_run",
            target_id=run_id,
        )
        return {
            "run": run,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_run_cancel",
            payload={"run_id": run_id},
            target_type="agent_run",
            target_id=run_id,
            status="failed",
            details={"error": str(exc)},
        )
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="agent_run_cancel",
            payload={"run_id": run_id},
            target_type="agent_run",
            target_id=run_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/agents/runs/{run_id}/resume")
def resume_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        existing = services.agent_manager.get_run(run_id=run_id)
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="agent_run",
            resource_id=run_id,
        )
        run = services.agent_manager.resume_run(run_id=run_id)
        receipt = _sign_action(
            request,
            action="agent_run_resume",
            payload={"run_id": run_id},
            actor=auth.user_id,
            target_type="agent_run",
            target_id=run_id,
        )
        return {
            "run": run,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_run_resume",
            payload={"run_id": run_id},
            target_type="agent_run",
            target_id=run_id,
            status="failed",
            details={"error": str(exc)},
        )
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="agent_run_resume",
            payload={"run_id": run_id},
            target_type="agent_run",
            target_id=run_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/debug/agents/runs/health")
def debug_agent_runs_health(
    request: Request,
    user_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        snapshot = services.agent_manager.run_health(
            user_id=effective_user_id,
            agent_id=agent_id,
            limit=limit,
        )
        return {
            "health": snapshot,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/agents/{agent_id}/chat")
def chat_agent(
    payload: AgentChatRequest,
    request: Request,
    agent_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = services.agent_manager.get_agent(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        assert_owner(
            owner_user_id=agent.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=agent_id,
        )
        result = services.agent_manager.chat(
            agent_id=agent_id,
            user_message=payload.message,
            user_id=effective_user_id,
            session_id=payload.session_id,
        )
        receipt = _sign_action(
            request,
            action="agent_chat_sync",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent",
            target_id=agent_id,
        )
        if isinstance(result, dict):
            payload_out = dict(result)
            payload_out["action_receipt"] = receipt
            payload_out["request_id"] = _request_id(request)
            return payload_out
        return {
            "result": result,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="agent_chat_sync",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent",
            target_id=agent_id,
            status="failed",
            details={"error": f"Agent not found: {agent_id}"},
        )
        raise
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_chat_sync",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent",
            target_id=agent_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="agent_chat_sync",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="agent",
            target_id=agent_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc
