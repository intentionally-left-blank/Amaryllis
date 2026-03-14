from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from runtime.auth import auth_context_from_request
from runtime.errors import ValidationError

router = APIRouter(tags=["backup"])


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
            target_type="backup",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class BackupRunRequest(BaseModel):
    trigger: str = Field(default="manual", min_length=1, max_length=64)
    verify: bool | None = None


class BackupVerifyRequest(BaseModel):
    backup_id: str | None = Field(default=None, min_length=1, max_length=128)


class BackupRestoreDrillRequest(BaseModel):
    backup_id: str | None = Field(default=None, min_length=1, max_length=128)


@router.get("/service/backup/status")
def backup_status(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    scheduler_status = (
        services.backup_scheduler.health_snapshot()
        if services.backup_scheduler is not None
        else {
            "started": False,
            "reason": "backup_disabled",
        }
    )
    return {
        "request_id": _request_id(request),
        "actor": auth.user_id,
        "scopes": sorted(auth.scopes),
        "enabled": services.config.backup_enabled,
        "scheduler": scheduler_status,
        "manager": services.backup_manager.status(),
    }


@router.get("/service/backup/backups")
def list_backups(request: Request, limit: int = 50) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    items = services.backup_manager.list_backups(limit=max(1, min(limit, 500)))
    return {
        "request_id": _request_id(request),
        "actor": auth.user_id,
        "scopes": sorted(auth.scopes),
        "items": items,
    }


@router.post("/service/backup/run")
def run_backup(payload: BackupRunRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    if not services.config.backup_enabled:
        raise ValidationError("Backup is disabled")

    trigger = str(payload.trigger).strip() or "manual"
    try:
        if services.backup_scheduler is not None:
            result = services.backup_scheduler.run_backup_now(
                trigger=trigger,
                actor=auth.user_id,
                request_id=_request_id(request),
            )
        else:
            result = services.backup_manager.create_backup(
                trigger=trigger,
                actor=auth.user_id,
                request_id=_request_id(request),
                verify=payload.verify,
            )
    except Exception as exc:
        _sign_action(
            request,
            action="backup_run",
            payload=payload.model_dump(exclude_none=True),
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc

    result["request_id"] = _request_id(request)
    result["action_receipt"] = _sign_action(
        request,
        action="backup_run",
        payload=payload.model_dump(exclude_none=True),
        actor=auth.user_id,
        target_id=str(result.get("backup_id") or ""),
    )
    return result


@router.post("/service/backup/verify")
def verify_backup(payload: BackupVerifyRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        result = services.backup_manager.verify_backup(backup_id=payload.backup_id)
    except Exception as exc:
        _sign_action(
            request,
            action="backup_verify",
            payload=payload.model_dump(exclude_none=True),
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
            target_id=payload.backup_id,
        )
        raise ValidationError(str(exc)) from exc

    result["request_id"] = _request_id(request)
    result["action_receipt"] = _sign_action(
        request,
        action="backup_verify",
        payload=payload.model_dump(exclude_none=True),
        actor=auth.user_id,
        target_id=payload.backup_id or str(result.get("backup_id") or ""),
    )
    return result


@router.post("/service/backup/restore-drill")
def restore_drill(payload: BackupRestoreDrillRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    if not services.config.backup_enabled:
        raise ValidationError("Backup is disabled")

    try:
        if services.backup_scheduler is not None:
            result = services.backup_scheduler.run_restore_drill_now(backup_id=payload.backup_id)
        else:
            result = services.backup_manager.run_restore_drill(backup_id=payload.backup_id)
    except Exception as exc:
        _sign_action(
            request,
            action="backup_restore_drill",
            payload=payload.model_dump(exclude_none=True),
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
            target_id=payload.backup_id,
        )
        raise ValidationError(str(exc)) from exc

    result["request_id"] = _request_id(request)
    result["action_receipt"] = _sign_action(
        request,
        action="backup_restore_drill",
        payload=payload.model_dump(exclude_none=True),
        actor=auth.user_id,
        target_id=payload.backup_id or str(result.get("backup_id") or ""),
    )
    return result
