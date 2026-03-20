from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError

router = APIRouter(tags=["agents"])
RUN_STATUSES: set[str] = {"queued", "running", "succeeded", "failed", "canceled"}


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


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
    stage: list[str] | None = Query(default=None),
    attempt: int | None = Query(default=None, ge=1, le=100),
    timeline_limit: int = Query(default=0, ge=0, le=5000),
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
        replay = services.agent_manager.replay_run_filtered(
            run_id=run_id,
            stages=stage,
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
