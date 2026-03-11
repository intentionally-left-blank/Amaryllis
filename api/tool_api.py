from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.errors import NotFoundError, PermissionDeniedError, ProviderError, ValidationError
from tools.tool_executor import PermissionRequiredError

router = APIRouter(tags=["tools"])


class MCPInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    permission_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None


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
        "request_id": str(getattr(request.state, "request_id", "")),
    }


@router.get("/tools/permissions/prompts")
def list_permission_prompts(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    services = request.app.state.services
    rows = services.tool_executor.list_permission_prompts(status=status, limit=limit)
    return {
        "items": rows,
        "count": len(rows),
        "request_id": str(getattr(request.state, "request_id", "")),
    }


@router.post("/tools/permissions/prompts/{prompt_id}/approve")
def approve_permission_prompt(
    request: Request,
    prompt_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        item = services.tool_executor.approve_permission_prompt(prompt_id=prompt_id)
        return {
            "prompt": item,
            "request_id": str(getattr(request.state, "request_id", "")),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/tools/permissions/prompts/{prompt_id}/deny")
def deny_permission_prompt(
    request: Request,
    prompt_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        item = services.tool_executor.deny_permission_prompt(prompt_id=prompt_id)
        return {
            "prompt": item,
            "request_id": str(getattr(request.state, "request_id", "")),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
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


@router.post("/mcp/tools/{tool_name}/invoke")
def invoke_mcp_tool(
    payload: MCPInvokeRequest,
    request: Request,
    tool_name: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    if services.tool_registry.get(tool_name) is None:
        raise NotFoundError(f"Tool not found: {tool_name}")

    try:
        result = services.tool_executor.execute(
            name=tool_name,
            arguments=payload.arguments,
            request_id=str(getattr(request.state, "request_id", "")),
            user_id=payload.user_id,
            session_id=payload.session_id,
            permission_id=payload.permission_id,
        )
        return {
            "result": result,
            "request_id": str(getattr(request.state, "request_id", "")),
        }
    except PermissionRequiredError as exc:
        raise PermissionDeniedError(str(exc)) from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc
