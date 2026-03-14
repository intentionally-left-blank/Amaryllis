from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import auth_context_from_request
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError

router = APIRouter(tags=["security"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _raise_security_error(exc: Exception) -> NoReturn:
    if isinstance(exc, AmaryllisError):
        raise exc
    message = str(exc or exc.__class__.__name__)
    normalized = message.lower()
    if isinstance(exc, ValueError):
        if "not found" in normalized:
            raise NotFoundError(message) from exc
        raise ValidationError(message) from exc
    raise ProviderError(message) from exc


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


class IdentityRotateRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


class IdentityRotateResponse(BaseModel):
    request_id: str
    rotation: dict[str, Any]
    action_receipt: dict[str, Any] = Field(default_factory=dict)


class SecretInventoryItem(BaseModel):
    secret_key: str
    provider: str
    is_required: bool
    source: str
    value_fingerprint: str | None = None
    value_present: bool
    last_rotated_at: str | None = None
    rotation_period_days: int
    expires_at: str | None = None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: str | None = None


class SecretInventoryResponse(BaseModel):
    request_id: str
    count: int
    summary: dict[str, Any] = Field(default_factory=dict)
    items: list[SecretInventoryItem] = Field(default_factory=list)


class SecretInventorySyncResponse(BaseModel):
    request_id: str
    synced_at: str
    count: int
    summary: dict[str, Any] = Field(default_factory=dict)
    items: list[SecretInventoryItem] = Field(default_factory=list)
    action_receipt: dict[str, Any] = Field(default_factory=dict)


class AuthTokenActivityItem(BaseModel):
    token_fingerprint: str
    user_id: str
    scopes: list[str] = Field(default_factory=list)
    first_seen_at: str
    last_seen_at: str
    last_request_id: str | None = None
    last_path: str | None = None
    last_method: str | None = None
    request_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthTokenActivityResponse(BaseModel):
    request_id: str
    count: int
    items: list[AuthTokenActivityItem] = Field(default_factory=list)


class AccessReviewStartRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=2000)
    stale_days: int | None = Field(default=None, ge=1, le=3650)


class AccessReviewCompleteRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=4000)
    decisions: dict[str, Any] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)


class AccessReviewResponse(BaseModel):
    request_id: str
    review: dict[str, Any] = Field(default_factory=dict)
    action_receipt: dict[str, Any] = Field(default_factory=dict)


class AccessReviewListResponse(BaseModel):
    request_id: str
    count: int
    items: list[dict[str, Any]] = Field(default_factory=list)


class IncidentOpenRequest(BaseModel):
    category: str = Field(default="security", min_length=1, max_length=100)
    severity: str = Field(default="medium", min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=4000)
    owner: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentAcknowledgeRequest(BaseModel):
    owner: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=4000)


class IncidentResolveRequest(BaseModel):
    resolution_summary: str | None = Field(default=None, max_length=4000)
    impact: str | None = Field(default=None, max_length=4000)
    containment: str | None = Field(default=None, max_length=4000)
    root_cause: str | None = Field(default=None, max_length=4000)
    recovery_actions: str | None = Field(default=None, max_length=4000)


class IncidentNoteRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    details: dict[str, Any] = Field(default_factory=dict)


class IncidentResponse(BaseModel):
    request_id: str
    incident: dict[str, Any] = Field(default_factory=dict)
    action_receipt: dict[str, Any] = Field(default_factory=dict)


class IncidentListResponse(BaseModel):
    request_id: str
    count: int
    items: list[dict[str, Any]] = Field(default_factory=list)


class ComplianceSnapshotResponse(BaseModel):
    request_id: str
    snapshot: dict[str, Any] = Field(default_factory=dict)


class EvidenceExportRequest(BaseModel):
    output_name: str | None = Field(default=None, max_length=255)
    window_days: int = Field(default=90, ge=1, le=3650)
    event_limit: int = Field(default=2000, ge=100, le=20000)


class EvidenceExportResponse(BaseModel):
    request_id: str
    result: dict[str, Any] = Field(default_factory=dict)


@router.get("/security/identity", response_model=IdentityResponse)
def get_security_identity(request: Request) -> IdentityResponse:
    services = request.app.state.services
    request_id = _request_id(request)
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
    request_id = _request_id(request)
    try:
        items = services.security_manager.list_audit_events(
            limit=limit,
            action=action,
            status=status,
            actor=actor,
        )
        typed = [SecurityAuditItem(**item) for item in items]
    except Exception as exc:
        _raise_security_error(exc)

    return SecurityAuditResponse(
        request_id=request_id,
        count=len(typed),
        items=typed,
    )


@router.post("/security/identity/rotate", response_model=IdentityRotateResponse)
def rotate_security_identity(
    request: Request,
    payload: IdentityRotateRequest,
) -> IdentityRotateResponse:
    services = request.app.state.services
    request_id = _request_id(request)
    auth = auth_context_from_request(request)
    try:
        result = services.security_manager.rotate_identity(
            actor=auth.user_id,
            request_id=request_id,
            reason=payload.reason,
        )
    except Exception as exc:
        _raise_security_error(exc)

    rotation = result.get("rotation")
    if not isinstance(rotation, dict):
        rotation = {}
    action_receipt = result.get("action_receipt")
    if not isinstance(action_receipt, dict):
        action_receipt = {}
    return IdentityRotateResponse(
        request_id=request_id,
        rotation=rotation,
        action_receipt=action_receipt,
    )


@router.get("/security/secrets", response_model=SecretInventoryResponse)
def list_security_secrets(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
    status: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    sync_first: bool = Query(default=False),
) -> SecretInventoryResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        payload = services.compliance_manager.list_secret_inventory(
            limit=limit,
            status=status,
            provider=provider,
            sync_first=sync_first,
            actor=auth.user_id,
            request_id=request_id,
        )
        items = [SecretInventoryItem(**item) for item in payload.get("items", [])]
    except Exception as exc:
        _raise_security_error(exc)
    return SecretInventoryResponse(
        request_id=request_id,
        count=len(items),
        summary=payload.get("summary", {}),
        items=items,
    )


@router.post("/security/secrets/sync", response_model=SecretInventorySyncResponse)
def sync_security_secrets(request: Request) -> SecretInventorySyncResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        payload = services.compliance_manager.sync_secret_inventory(
            actor=auth.user_id,
            request_id=request_id,
        )
        items = [SecretInventoryItem(**item) for item in payload.get("items", [])]
    except Exception as exc:
        _raise_security_error(exc)
    return SecretInventorySyncResponse(
        request_id=request_id,
        synced_at=str(payload.get("synced_at") or ""),
        count=len(items),
        summary=payload.get("summary", {}),
        items=items,
        action_receipt=payload.get("action_receipt", {}),
    )


@router.get("/security/auth/tokens/activity", response_model=AuthTokenActivityResponse)
def list_auth_token_activity(
    request: Request,
    limit: int = Query(default=200, ge=1, le=5000),
    user_id: str | None = Query(default=None),
) -> AuthTokenActivityResponse:
    services = request.app.state.services
    request_id = _request_id(request)
    try:
        items = services.compliance_manager.list_auth_token_activity(limit=limit, user_id=user_id)
        typed = [AuthTokenActivityItem(**item) for item in items]
    except Exception as exc:
        _raise_security_error(exc)
    return AuthTokenActivityResponse(
        request_id=request_id,
        count=len(typed),
        items=typed,
    )


@router.post("/security/access-reviews/start", response_model=AccessReviewResponse)
def start_access_review(request: Request, payload: AccessReviewStartRequest) -> AccessReviewResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        result = services.compliance_manager.start_access_review(
            reviewer=auth.user_id,
            summary=payload.summary,
            stale_days=payload.stale_days,
            request_id=request_id,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return AccessReviewResponse(
        request_id=request_id,
        review=result.get("review", {}),
        action_receipt=result.get("action_receipt", {}),
    )


@router.post("/security/access-reviews/{review_id}/complete", response_model=AccessReviewResponse)
def complete_access_review(
    review_id: str,
    request: Request,
    payload: AccessReviewCompleteRequest,
) -> AccessReviewResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        result = services.compliance_manager.complete_access_review(
            review_id=review_id,
            reviewer=auth.user_id,
            summary=payload.summary,
            decisions=payload.decisions,
            findings=payload.findings,
            request_id=request_id,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return AccessReviewResponse(
        request_id=request_id,
        review=result.get("review", {}),
        action_receipt=result.get("action_receipt", {}),
    )


@router.get("/security/access-reviews", response_model=AccessReviewListResponse)
def list_access_reviews(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    status: str | None = Query(default=None),
) -> AccessReviewListResponse:
    services = request.app.state.services
    request_id = _request_id(request)
    try:
        items = services.compliance_manager.list_access_reviews(limit=limit, status=status)
    except Exception as exc:
        _raise_security_error(exc)
    return AccessReviewListResponse(
        request_id=request_id,
        count=len(items),
        items=items,
    )


@router.get("/security/access-reviews/{review_id}", response_model=AccessReviewResponse)
def get_access_review(review_id: str, request: Request) -> AccessReviewResponse:
    services = request.app.state.services
    request_id = _request_id(request)
    try:
        review = services.compliance_manager.get_access_review(review_id=review_id)
        if review is None:
            raise ValueError(f"Access review not found: {review_id}")
    except Exception as exc:
        _raise_security_error(exc)
    return AccessReviewResponse(
        request_id=request_id,
        review=review,
        action_receipt={},
    )


@router.post("/security/incidents/open", response_model=IncidentResponse)
def open_incident(request: Request, payload: IncidentOpenRequest) -> IncidentResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        result = services.compliance_manager.open_incident(
            category=payload.category,
            severity=payload.severity,
            title=payload.title,
            description=payload.description,
            owner=payload.owner,
            actor=auth.user_id,
            request_id=request_id,
            metadata=payload.metadata,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return IncidentResponse(
        request_id=request_id,
        incident=result.get("incident", {}),
        action_receipt=result.get("action_receipt", {}),
    )


@router.post("/security/incidents/{incident_id}/ack", response_model=IncidentResponse)
def acknowledge_incident(
    incident_id: str,
    request: Request,
    payload: IncidentAcknowledgeRequest,
) -> IncidentResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        result = services.compliance_manager.acknowledge_incident(
            incident_id=incident_id,
            actor=auth.user_id,
            owner=payload.owner,
            note=payload.note,
            request_id=request_id,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return IncidentResponse(
        request_id=request_id,
        incident=result.get("incident", {}),
        action_receipt=result.get("action_receipt", {}),
    )


@router.post("/security/incidents/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(
    incident_id: str,
    request: Request,
    payload: IncidentResolveRequest,
) -> IncidentResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        result = services.compliance_manager.resolve_incident(
            incident_id=incident_id,
            actor=auth.user_id,
            resolution_summary=payload.resolution_summary,
            impact=payload.impact,
            containment=payload.containment,
            root_cause=payload.root_cause,
            recovery_actions=payload.recovery_actions,
            request_id=request_id,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return IncidentResponse(
        request_id=request_id,
        incident=result.get("incident", {}),
        action_receipt=result.get("action_receipt", {}),
    )


@router.post("/security/incidents/{incident_id}/notes", response_model=IncidentResponse)
def add_incident_note(
    incident_id: str,
    request: Request,
    payload: IncidentNoteRequest,
) -> IncidentResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        result = services.compliance_manager.add_incident_note(
            incident_id=incident_id,
            actor=auth.user_id,
            message=payload.message,
            details=payload.details,
            request_id=request_id,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return IncidentResponse(
        request_id=request_id,
        incident=result.get("incident", {}),
        action_receipt=result.get("action_receipt", {}),
    )


@router.get("/security/incidents", response_model=IncidentListResponse)
def list_incidents(
    request: Request,
    limit: int = Query(default=200, ge=1, le=2000),
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> IncidentListResponse:
    services = request.app.state.services
    request_id = _request_id(request)
    try:
        items = services.compliance_manager.list_incidents(
            limit=limit,
            status=status,
            severity=severity,
            category=category,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return IncidentListResponse(
        request_id=request_id,
        count=len(items),
        items=items,
    )


@router.get("/security/incidents/{incident_id}", response_model=IncidentResponse)
def get_incident(incident_id: str, request: Request) -> IncidentResponse:
    services = request.app.state.services
    request_id = _request_id(request)
    try:
        incident = services.compliance_manager.get_incident(incident_id=incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
    except Exception as exc:
        _raise_security_error(exc)
    return IncidentResponse(
        request_id=request_id,
        incident=incident,
        action_receipt={},
    )


@router.get("/security/compliance/snapshot", response_model=ComplianceSnapshotResponse)
def compliance_snapshot(request: Request) -> ComplianceSnapshotResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        snapshot = services.compliance_manager.compliance_snapshot(
            request_id=request_id,
            actor=auth.user_id,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return ComplianceSnapshotResponse(
        request_id=request_id,
        snapshot=snapshot,
    )


@router.post("/security/compliance/evidence/export", response_model=EvidenceExportResponse)
def export_evidence(
    request: Request,
    payload: EvidenceExportRequest,
) -> EvidenceExportResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    try:
        result = services.compliance_manager.export_evidence_bundle(
            actor=auth.user_id,
            request_id=request_id,
            output_name=payload.output_name,
            window_days=payload.window_days,
            event_limit=payload.event_limit,
        )
    except Exception as exc:
        _raise_security_error(exc)
    return EvidenceExportResponse(
        request_id=request_id,
        result=result,
    )
