from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError
from supervisor.task_graph_manager import SUPERVISOR_GRAPH_STATUSES, SUPERVISOR_NODE_STATUSES

router = APIRouter(tags=["supervisor"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _sign_action(
    request: Request,
    *,
    action: str,
    payload: dict[str, Any],
    actor: str | None = None,
    status: str = "succeeded",
    details: dict[str, Any] | None = None,
    target_id: str | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.security_manager.signed_action(
            action=action,
            payload=payload,
            request_id=_request_id(request),
            actor=actor,
            target_type="supervisor_graph",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class SupervisorRunBudgetRequest(BaseModel):
    max_tokens: int | None = Field(default=None, ge=256, le=2_000_000)
    max_duration_sec: float | None = Field(default=None, ge=10.0, le=86_400.0)
    max_tool_calls: int | None = Field(default=None, ge=1, le=200)
    max_tool_errors: int | None = Field(default=None, ge=0, le=200)


class SupervisorGraphNodeRequest(BaseModel):
    node_id: str | None = Field(default=None, min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=20_000)
    depends_on: list[str] = Field(default_factory=list)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    budget: SupervisorRunBudgetRequest | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupervisorGraphCreateRequest(BaseModel):
    user_id: str | None = None
    objective: str = Field(min_length=1, max_length=20_000)
    nodes: list[SupervisorGraphNodeRequest] = Field(min_length=1, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SupervisorGraphLaunchRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=512)


class SupervisorGraphTickRequest(BaseModel):
    noop: bool = True


def _validate_agent_ownership_for_nodes(
    *,
    request: Request,
    effective_user_id: str,
    nodes: list[SupervisorGraphNodeRequest],
) -> None:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    for index, node in enumerate(nodes):
        agent_id = str(node.agent_id or "").strip()
        if not agent_id:
            raise ValidationError(f"nodes[{index}].agent_id is required")
        agent = services.agent_manager.get_agent(agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {agent_id}")
        assert_owner(
            owner_user_id=agent.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=agent_id,
        )
        owner = str(agent.user_id or "").strip()
        if owner and owner != effective_user_id:
            node_id = str(node.node_id or f"node-{index + 1}")
            raise ValidationError(
                f"Node '{node_id}' uses agent '{agent_id}' owned by another user."
            )


@router.get("/supervisor/graphs/contract")
def supervisor_contract(request: Request) -> dict[str, Any]:
    _ = auth_context_from_request(request)
    return {
        "graph_statuses": sorted(SUPERVISOR_GRAPH_STATUSES),
        "node_statuses": sorted(SUPERVISOR_NODE_STATUSES),
        "checkpoint_resume": {
            "enabled": True,
            "store": "sqlite.supervisor_graphs",
            "mode": "runtime_auto_hydrate",
        },
        "request_id": _request_id(request),
    }


@router.post("/supervisor/graphs/create")
def create_supervisor_graph(payload: SupervisorGraphCreateRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    sign_payload = {
        **payload.model_dump(exclude_none=True),
        "user_id": effective_user_id,
    }
    try:
        _validate_agent_ownership_for_nodes(
            request=request,
            effective_user_id=effective_user_id,
            nodes=payload.nodes,
        )
        graph = services.supervisor_manager.create_graph(
            user_id=effective_user_id,
            objective=payload.objective,
            nodes=[
                {
                    "node_id": node.node_id,
                    "agent_id": node.agent_id,
                    "message": node.message,
                    "depends_on": list(node.depends_on),
                    "max_attempts": node.max_attempts,
                    "budget": node.budget.model_dump(exclude_none=True) if node.budget is not None else None,
                    "metadata": dict(node.metadata),
                }
                for node in payload.nodes
            ],
            metadata=dict(payload.metadata),
            request_id=_request_id(request),
            actor=auth.user_id,
        )
        graph_id = str(graph.get("id") or "")
        receipt = _sign_action(
            request,
            action="supervisor_graph_create",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=graph_id,
            details={"node_count": len(payload.nodes)},
        )
        return {
            "supervisor_graph": graph,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "graph_statuses": sorted(SUPERVISOR_GRAPH_STATUSES),
            "node_statuses": sorted(SUPERVISOR_NODE_STATUSES),
        }
    except (ValidationError, NotFoundError):
        _sign_action(
            request,
            action="supervisor_graph_create",
            payload=sign_payload,
            actor=auth.user_id,
            status="failed",
            details={"error": "validation_failed"},
        )
        raise
    except ValueError as exc:
        _sign_action(
            request,
            action="supervisor_graph_create",
            payload=sign_payload,
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="supervisor_graph_create",
            payload=sign_payload,
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/supervisor/graphs")
def list_supervisor_graphs(
    request: Request,
    user_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=2000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        items = services.supervisor_manager.list_graphs(
            user_id=effective_user_id,
            status=status,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
            "graph_statuses": sorted(SUPERVISOR_GRAPH_STATUSES),
            "node_statuses": sorted(SUPERVISOR_NODE_STATUSES),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/supervisor/graphs/{graph_id}")
def get_supervisor_graph(
    request: Request,
    graph_id: str = Path(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        graph = services.supervisor_manager.get_graph(graph_id=graph_id)
        assert_owner(
            owner_user_id=str(graph.get("user_id") or ""),
            auth=auth,
            resource_name="supervisor_graph",
            resource_id=graph_id,
        )
        return {
            "supervisor_graph": graph,
            "request_id": _request_id(request),
            "graph_statuses": sorted(SUPERVISOR_GRAPH_STATUSES),
            "node_statuses": sorted(SUPERVISOR_NODE_STATUSES),
        }
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/supervisor/graphs/{graph_id}/launch")
def launch_supervisor_graph(
    payload: SupervisorGraphLaunchRequest,
    request: Request,
    graph_id: str = Path(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    sign_payload = {
        "graph_id": graph_id,
        "session_id": payload.session_id,
    }
    try:
        existing = services.supervisor_manager.get_graph(graph_id=graph_id)
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="supervisor_graph",
            resource_id=graph_id,
        )
        graph = services.supervisor_manager.launch_graph(
            graph_id=graph_id,
            user_id=str(existing.get("user_id") or ""),
            session_id=payload.session_id,
            request_id=_request_id(request),
            actor=auth.user_id,
        )
        receipt = _sign_action(
            request,
            action="supervisor_graph_launch",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=graph_id,
            details={"status": graph.get("status")},
        )
        return {
            "supervisor_graph": graph,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "graph_statuses": sorted(SUPERVISOR_GRAPH_STATUSES),
            "node_statuses": sorted(SUPERVISOR_NODE_STATUSES),
        }
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        _sign_action(
            request,
            action="supervisor_graph_launch",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=graph_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="supervisor_graph_launch",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=graph_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/supervisor/graphs/{graph_id}/tick")
def tick_supervisor_graph(
    payload: SupervisorGraphTickRequest,
    request: Request,
    graph_id: str = Path(..., min_length=1, max_length=128),
) -> dict[str, Any]:
    _ = payload
    services = request.app.state.services
    auth = auth_context_from_request(request)
    sign_payload = {
        "graph_id": graph_id,
    }
    try:
        existing = services.supervisor_manager.get_graph(graph_id=graph_id)
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="supervisor_graph",
            resource_id=graph_id,
        )
        graph = services.supervisor_manager.tick_graph(
            graph_id=graph_id,
            user_id=str(existing.get("user_id") or ""),
            request_id=_request_id(request),
            actor=auth.user_id,
        )
        receipt = _sign_action(
            request,
            action="supervisor_graph_tick",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=graph_id,
            details={"status": graph.get("status")},
        )
        return {
            "supervisor_graph": graph,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "graph_statuses": sorted(SUPERVISOR_GRAPH_STATUSES),
            "node_statuses": sorted(SUPERVISOR_NODE_STATUSES),
        }
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        _sign_action(
            request,
            action="supervisor_graph_tick",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=graph_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="supervisor_graph_tick",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=graph_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc
