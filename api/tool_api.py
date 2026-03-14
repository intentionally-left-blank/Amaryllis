from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, PermissionDeniedError, ProviderError, ValidationError
from tools.tool_executor import PermissionRequiredError, ToolBudgetLimitError

router = APIRouter(tags=["tools"])


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


class MCPInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    permission_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None


class ToolGuardrailsDebugResponse(BaseModel):
    request_id: str
    approval_enforcement_mode: str
    isolation_policy: dict[str, Any]
    sandbox: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any]
    plugin_signing: dict[str, Any] = Field(default_factory=dict)


@router.get("/tools")
def list_tools(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    items = []
    for tool in services.tool_registry.list():
        items.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "source": tool.source,
                "risk_level": tool.risk_level,
                "approval_mode": tool.approval_mode,
                "isolation": tool.isolation,
            }
        )
    items.sort(key=lambda item: item["name"])
    return {
        "items": items,
        "count": len(items),
        "request_id": _request_id(request),
    }


@router.get("/tools/permissions/prompts")
def list_permission_prompts(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    rows = services.tool_executor.list_permission_prompts(status=status, limit=limit)
    if not auth.is_admin:
        rows = [item for item in rows if str(item.get("user_id") or "") == auth.user_id]
    return {
        "items": rows,
        "count": len(rows),
        "request_id": _request_id(request),
    }


@router.post("/tools/permissions/prompts/{prompt_id}/approve")
def approve_permission_prompt(
    request: Request,
    prompt_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        prompts = services.tool_executor.list_permission_prompts(status=None, limit=2000)
        match = next((item for item in prompts if str(item.get("id") or "") == prompt_id), None)
        if match is None:
            raise NotFoundError(f"Permission prompt not found: {prompt_id}")
        assert_owner(
            owner_user_id=str(match.get("user_id") or ""),
            auth=auth,
            resource_name="tool_permission_prompt",
            resource_id=prompt_id,
        )
        item = services.tool_executor.approve_permission_prompt(prompt_id=prompt_id)
        receipt = _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
        )
        return {
            "prompt": item,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": f"Permission prompt not found: {prompt_id}"},
        )
        raise
    except ValueError as exc:
        _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/tools/permissions/prompts/{prompt_id}/deny")
def deny_permission_prompt(
    request: Request,
    prompt_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        prompts = services.tool_executor.list_permission_prompts(status=None, limit=2000)
        match = next((item for item in prompts if str(item.get("id") or "") == prompt_id), None)
        if match is None:
            raise NotFoundError(f"Permission prompt not found: {prompt_id}")
        assert_owner(
            owner_user_id=str(match.get("user_id") or ""),
            auth=auth,
            resource_name="tool_permission_prompt",
            resource_id=prompt_id,
        )
        item = services.tool_executor.deny_permission_prompt(prompt_id=prompt_id)
        receipt = _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
        )
        return {
            "prompt": item,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": f"Permission prompt not found: {prompt_id}"},
        )
        raise
    except ValueError as exc:
        _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/mcp/tools")
def list_mcp_tools(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    items = []
    for tool in services.tool_registry.list():
        items.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "source": tool.source,
                "risk_level": tool.risk_level,
                "approval_mode": tool.approval_mode,
                "isolation": tool.isolation,
            }
        )
    items.sort(key=lambda item: item["name"])
    return {
        "items": items,
        "count": len(items),
    }


@router.get("/debug/tools/guardrails", response_model=ToolGuardrailsDebugResponse)
def debug_tool_guardrails(
    request: Request,
    user_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    scope_request_id: str | None = Query(default=None),
    scopes_limit: int = Query(default=20, ge=1, le=200),
    top_tools_limit: int = Query(default=5, ge=1, le=20),
) -> ToolGuardrailsDebugResponse:
    services = request.app.state.services
    snapshot = services.tool_executor.debug_guardrails(
        request_id=scope_request_id,
        user_id=user_id,
        session_id=session_id,
        scopes_limit=scopes_limit,
        top_tools_limit=top_tools_limit,
    )
    return ToolGuardrailsDebugResponse(
        request_id=_request_id(request),
        approval_enforcement_mode=str(snapshot.get("approval_enforcement_mode", "prompt_and_allow")),
        isolation_policy=dict(snapshot.get("isolation_policy", {})),
        sandbox=dict(snapshot.get("sandbox", {})),
        budget=dict(snapshot.get("budget", {})),
        plugin_signing=dict(snapshot.get("plugin_signing", {})),
    )


@router.get("/debug/tools/mcp-health")
def debug_mcp_health(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    registry = getattr(services, "mcp_registry", None)
    if registry is None:
        return {
            "enabled": False,
            "health": {"items": [], "count": 0},
            "request_id": _request_id(request),
        }
    return {
        "enabled": True,
        "health": registry.debug_health(),
        "request_id": _request_id(request),
    }


@router.post("/mcp/tools/{tool_name}/invoke")
def invoke_mcp_tool(
    payload: MCPInvokeRequest,
    request: Request,
    tool_name: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    if services.tool_registry.get(tool_name) is None:
        raise NotFoundError(f"Tool not found: {tool_name}")

    try:
        result = services.tool_executor.execute(
            name=tool_name,
            arguments=payload.arguments,
            request_id=_request_id(request),
            user_id=effective_user_id,
            session_id=payload.session_id,
            permission_id=payload.permission_id,
        )
        receipt = _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            details={
                "session_id": payload.session_id,
                "permission_id": payload.permission_id,
            },
        )
        return {
            "result": result,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except PermissionRequiredError as exc:
        _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            status="failed",
            details={"error": str(exc), "session_id": payload.session_id},
        )
        raise PermissionDeniedError(str(exc)) from exc
    except ToolBudgetLimitError as exc:
        _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            status="failed",
            details={"error": str(exc), "session_id": payload.session_id},
        )
        raise PermissionDeniedError(str(exc)) from exc
    except ValueError as exc:
        _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            status="failed",
            details={"error": str(exc), "session_id": payload.session_id},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            status="failed",
            details={"error": str(exc), "session_id": payload.session_id},
        )
        raise ProviderError(str(exc)) from exc
