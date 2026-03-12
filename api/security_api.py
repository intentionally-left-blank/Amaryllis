from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from runtime.errors import ProviderError

router = APIRouter(tags=["security"])


class IdentityResponse(BaseModel):
    request_id: str
    identity: dict[str, Any]


class SecurityAuditItem(BaseModel):
    id: int
    event_type: str
    action: str | None = None
    actor: str | None = None
    request_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    status: str
    details: dict[str, Any] = Field(default_factory=dict)
    signature: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SecurityAuditResponse(BaseModel):
    request_id: str
    count: int
    items: list[SecurityAuditItem]


@router.get("/security/identity", response_model=IdentityResponse)
def get_security_identity(request: Request) -> IdentityResponse:
    services = request.app.state.services
    request_id = str(getattr(request.state, "request_id", ""))
    return IdentityResponse(
        request_id=request_id,
        identity=services.security_manager.identity_info(),
    )


@router.get("/security/audit", response_model=SecurityAuditResponse)
def list_security_audit(
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    action: str | None = Query(default=None),
    status: str | None = Query(default=None),
    actor: str | None = Query(default=None),
) -> SecurityAuditResponse:
    services = request.app.state.services
    request_id = str(getattr(request.state, "request_id", ""))
    try:
        items = services.security_manager.list_audit_events(
            limit=limit,
            action=action,
            status=status,
            actor=actor,
        )
        typed = [SecurityAuditItem(**item) for item in items]
    except Exception as exc:
        raise ProviderError(str(exc)) from exc

    return SecurityAuditResponse(
        request_id=request_id,
        count=len(typed),
        items=typed,
    )
