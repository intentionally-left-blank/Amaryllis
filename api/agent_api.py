from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.errors import NotFoundError, ProviderError, ValidationError

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


class AgentRunCreateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_id: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)


@router.post("/agents/create")
def create_agent(payload: CreateAgentRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        agent = services.agent_manager.create_agent(
            name=payload.name,
            system_prompt=payload.system_prompt,
            model=payload.model,
            tools=payload.tools,
            user_id=payload.user_id,
        )
        receipt = _sign_action(
            request,
            action="agent_create",
            payload=payload.model_dump(),
            actor=payload.user_id,
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
            payload=payload.model_dump(),
            actor=payload.user_id,
            target_type="agent",
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="agent_create",
            payload=payload.model_dump(),
            actor=payload.user_id,
            target_type="agent",
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/agents")
def list_agents(request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    services = request.app.state.services
    agents = services.agent_manager.list_agents(user_id=user_id)
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
    try:
        run = services.agent_manager.create_run(
            agent_id=agent_id,
            user_message=payload.message,
            user_id=payload.user_id,
            session_id=payload.session_id,
            max_attempts=payload.max_attempts,
        )
        receipt = _sign_action(
            request,
            action="agent_run_create",
            payload=payload.model_dump(),
            actor=payload.user_id,
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
            payload=payload.model_dump(),
            actor=payload.user_id,
            target_type="agent_run",
            details={"agent_id": agent_id, "error": str(exc)},
            status="failed",
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="agent_run_create",
            payload=payload.model_dump(),
            actor=payload.user_id,
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
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        if status is not None:
            normalized_status = status.strip().lower()
            if normalized_status not in RUN_STATUSES:
                allowed = ", ".join(sorted(RUN_STATUSES))
                raise ValidationError(f"Invalid status: {status}. Allowed: {allowed}")
            status = normalized_status
        runs = services.agent_manager.list_runs(
            user_id=user_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )
        return {
            "items": runs,
            "count": len(runs),
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}")
def get_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        run = services.agent_manager.get_run(run_id=run_id)
        return {
            "run": run,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/agents/runs/{run_id}/replay")
def replay_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        replay = services.agent_manager.replay_run(run_id=run_id)
        return {
            "replay": replay,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/agents/runs/{run_id}/cancel")
def cancel_agent_run(
    request: Request,
    run_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        run = services.agent_manager.cancel_run(run_id=run_id)
        receipt = _sign_action(
            request,
            action="agent_run_cancel",
            payload={"run_id": run_id},
            actor=str(run.get("user_id") or ""),
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
    try:
        run = services.agent_manager.resume_run(run_id=run_id)
        receipt = _sign_action(
            request,
            action="agent_run_resume",
            payload={"run_id": run_id},
            actor=str(run.get("user_id") or ""),
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


@router.post("/agents/{agent_id}/chat")
def chat_agent(
    payload: AgentChatRequest,
    request: Request,
    agent_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        result = services.agent_manager.chat(
            agent_id=agent_id,
            user_message=payload.message,
            user_id=payload.user_id,
            session_id=payload.session_id,
        )
        receipt = _sign_action(
            request,
            action="agent_chat_sync",
            payload=payload.model_dump(),
            actor=payload.user_id,
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
    except ValueError as exc:
        _sign_action(
            request,
            action="agent_chat_sync",
            payload=payload.model_dump(),
            actor=payload.user_id,
            target_type="agent",
            target_id=agent_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="agent_chat_sync",
            payload=payload.model_dump(),
            actor=payload.user_id,
            target_type="agent",
            target_id=agent_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc
