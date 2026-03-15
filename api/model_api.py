from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from runtime.auth import auth_context_from_request
from runtime.errors import ProviderError, ValidationError

router = APIRouter(tags=["models"])


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
            target_type="model",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class DownloadModelRequest(BaseModel):
    model_id: str = Field(min_length=1)
    provider: str | None = None


class LoadModelRequest(BaseModel):
    model_id: str = Field(min_length=1)
    provider: str | None = None


class ModelRouteRequest(BaseModel):
    mode: str = Field(default="balanced")
    provider: str | None = None
    model: str | None = None
    require_stream: bool = True
    require_tools: bool = False
    prefer_local: bool | None = None
    min_params_b: float | None = Field(default=None, ge=0.0)
    max_params_b: float | None = Field(default=None, ge=0.0)
    include_suggested: bool = False
    limit_per_provider: int = Field(default=120, ge=1, le=500)


class ModelFailoverDebugResponse(BaseModel):
    request_id: str
    diagnostics: dict[str, Any]


class ModelDownloadJob(BaseModel):
    id: str
    provider: str
    model: str
    status: str
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    completed_bytes: int | None = None
    total_bytes: int | None = None
    message: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    finished_at: str | None = None


class ModelDownloadJobResponse(BaseModel):
    request_id: str
    job: ModelDownloadJob
    already_running: bool = False


class ModelDownloadJobsListResponse(BaseModel):
    request_id: str
    items: list[ModelDownloadJob]
    count: int


@router.get("/models")
def list_models(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    payload = services.model_manager.list_models()
    payload["request_id"] = _request_id(request)
    return payload


@router.get("/models/capabilities")
def model_capabilities(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    return {
        "active": {
            "provider": services.model_manager.active_provider,
            "model": services.model_manager.active_model,
        },
        "providers": services.model_manager.provider_capabilities(),
        "request_id": _request_id(request),
    }


@router.get("/models/capability-matrix")
def capability_matrix(
    request: Request,
    include_suggested: bool = True,
    limit_per_provider: int = 120,
) -> dict[str, Any]:
    services = request.app.state.services
    payload = services.model_manager.model_capability_matrix(
        include_suggested=include_suggested,
        limit_per_provider=max(1, min(limit_per_provider, 500)),
    )
    payload["request_id"] = _request_id(request)
    return payload


@router.post("/models/route")
def model_route(payload: ModelRouteRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        route = services.model_manager.choose_route(
            mode=payload.mode,
            provider=payload.provider,
            model=payload.model,
            require_stream=payload.require_stream,
            require_tools=payload.require_tools,
            prefer_local=payload.prefer_local,
            min_params_b=payload.min_params_b,
            max_params_b=payload.max_params_b,
            include_suggested=payload.include_suggested,
            limit_per_provider=payload.limit_per_provider,
        )
        route["request_id"] = _request_id(request)
        return route
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/debug/models/failover", response_model=ModelFailoverDebugResponse)
def debug_model_failover(
    request: Request,
    session_id: str | None = None,
    limit: int = 100,
) -> ModelFailoverDebugResponse:
    services = request.app.state.services
    diagnostics = services.model_manager.debug_failover_state(
        session_id=session_id,
        limit=max(1, min(limit, 500)),
    )
    return ModelFailoverDebugResponse(
        request_id=_request_id(request),
        diagnostics=diagnostics,
    )


@router.post("/models/download")
def download_model(payload: DownloadModelRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        result = services.model_manager.download_model(
            model_id=payload.model_id,
            provider=payload.provider,
        )
        result["action_receipt"] = _sign_action(
            request,
            action="model_download",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
        )
        result["request_id"] = _request_id(request)
        return result
    except ValueError as exc:
        _sign_action(
            request,
            action="model_download",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="model_download",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/models/download/start", response_model=ModelDownloadJobResponse)
def start_model_download(payload: DownloadModelRequest, request: Request) -> ModelDownloadJobResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        result = services.model_manager.start_model_download(
            model_id=payload.model_id,
            provider=payload.provider,
        )
        _sign_action(
            request,
            action="model_download_start",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
        )
        return ModelDownloadJobResponse(
            request_id=_request_id(request),
            job=ModelDownloadJob(**result["job"]),
            already_running=bool(result.get("already_running", False)),
        )
    except ValueError as exc:
        _sign_action(
            request,
            action="model_download_start",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="model_download_start",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/models/download/{job_id}", response_model=ModelDownloadJobResponse)
def get_model_download(job_id: str, request: Request) -> ModelDownloadJobResponse:
    services = request.app.state.services
    try:
        job = services.model_manager.get_model_download_job(job_id=job_id)
        return ModelDownloadJobResponse(
            request_id=_request_id(request),
            job=ModelDownloadJob(**job),
            already_running=False,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/models/downloads", response_model=ModelDownloadJobsListResponse)
def list_model_downloads(
    request: Request,
    limit: int = 100,
) -> ModelDownloadJobsListResponse:
    services = request.app.state.services
    try:
        payload = services.model_manager.list_model_download_jobs(limit=max(1, min(limit, 500)))
        rows = [ModelDownloadJob(**item) for item in payload.get("items", [])]
        return ModelDownloadJobsListResponse(
            request_id=_request_id(request),
            items=rows,
            count=int(payload.get("count", len(rows))),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/models/load")
def load_model(payload: LoadModelRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        result = services.model_manager.load_model(
            model_id=payload.model_id,
            provider=payload.provider,
        )
        result["action_receipt"] = _sign_action(
            request,
            action="model_load",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
        )
        result["request_id"] = _request_id(request)
        return result
    except ValueError as exc:
        _sign_action(
            request,
            action="model_load",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="model_load",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.model_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc
