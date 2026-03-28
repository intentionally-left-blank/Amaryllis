from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from runtime.auth import auth_context_from_request
from runtime.errors import ProviderError, ValidationError

router = APIRouter(tags=["models"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generation_loop_contract_payload(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    model_manager = services.model_manager
    active_provider = str(getattr(model_manager, "active_provider", "") or "unknown")
    active_model = str(getattr(model_manager, "active_model", "") or "unknown")

    capabilities_getter = getattr(model_manager, "provider_capabilities", None)
    raw_capabilities = capabilities_getter() if callable(capabilities_getter) else {}
    capabilities = raw_capabilities if isinstance(raw_capabilities, dict) else {}

    providers: dict[str, Any] = {}
    passing = 0
    warning = 0
    for provider_name in sorted(capabilities.keys()):
        cap_raw = capabilities.get(provider_name)
        cap = cap_raw if isinstance(cap_raw, dict) else {}
        supports_stream = bool(cap.get("supports_stream", False))
        supports_tools = bool(cap.get("supports_tools", False))
        supports_load = bool(cap.get("supports_load", False))

        issues: list[str] = []
        if not supports_stream:
            issues.append("streaming_not_supported")
        if not supports_load:
            issues.append("load_model_not_supported")
        status = "pass" if not issues else "warn"
        if status == "pass":
            passing += 1
        else:
            warning += 1

        providers[str(provider_name)] = {
            "capabilities": {
                "local": bool(cap.get("local", False)),
                "supports_download": bool(cap.get("supports_download", False)),
                "supports_load": supports_load,
                "supports_stream": supports_stream,
                "supports_tools": supports_tools,
                "requires_api_key": bool(cap.get("requires_api_key", False)),
            },
            "conformance": {
                "status": status,
                "issues": issues,
                "checks": {
                    "decode_streaming": supports_stream,
                    "tool_calling_grammar_path": supports_tools,
                    "runtime_load_switch": supports_load,
                },
            },
        }

    return {
        "contract_version": "generation_loop_contract_v1",
        "generated_at": _utc_now_iso(),
        "active": {
            "provider": active_provider,
            "model": active_model,
        },
        "contract": {
            "stages": [
                "prefill",
                "decode",
                "finalize",
            ],
            "cache": {
                "kv_cache": "required",
                "cache_policy": "runtime_managed",
                "pressure_signal_contract": "generation_loop_metrics.kv_cache.pressure_state",
                "pressure_states": [
                    "low",
                    "elevated",
                    "high",
                    "critical",
                ],
                "pressure_budget_units": "estimated_tokens",
            },
            "fallback": {
                "deterministic_semantics": True,
                "ordered_resolution": [
                    "explicit_target",
                    "routing_selected",
                    "fallback_candidates",
                ],
            },
            "streaming": {
                "required_for_portability": True,
                "event_channel": "sse_chunked",
            },
            "tool_calling": {
                "grammar_contract": "provider_capability_gated",
                "permission_boundary": "tool_policy_and_sandbox",
            },
        },
        "modes": [
            "balanced",
            "local_first",
            "quality_first",
            "coding",
            "reasoning",
        ],
        "providers": providers,
        "summary": {
            "providers_total": len(providers),
            "providers_passing": passing,
            "providers_warning": warning,
        },
    }


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


class ModelArtifactAdmissionRequest(BaseModel):
    manifest: dict[str, Any] = Field(default_factory=dict)
    strict: bool = True
    artifact_root: str | None = None


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


class InstallModelPackageRequest(BaseModel):
    package_id: str = Field(min_length=1)
    activate: bool = True


class OnboardingActivateRequest(BaseModel):
    profile: str | None = None
    include_remote_providers: bool = True
    limit: int = Field(default=120, ge=1, le=500)
    require_metadata: bool | None = None
    activate: bool = True
    run_smoke_test: bool = True
    smoke_prompt: str | None = Field(default=None, max_length=2000)


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


class ModelArtifactAdmissionResponse(BaseModel):
    request_id: str
    admitted: bool
    mode: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    action_receipt: dict[str, Any] = Field(default_factory=dict)


@router.get("/models")
def list_models(
    request: Request,
    include_suggested: bool = True,
    include_remote_providers: bool = True,
    item_limit: int = 80,
) -> dict[str, Any]:
    services = request.app.state.services
    normalized_limit = max(1, min(item_limit, 500))
    try:
        payload = services.model_manager.list_models(
            include_suggested=include_suggested,
            include_remote_providers=include_remote_providers,
            max_items_per_provider=normalized_limit,
        )
    except TypeError:
        payload = services.model_manager.list_models(
            include_suggested=include_suggested,
            include_remote_providers=include_remote_providers,
        )
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


@router.get("/models/generation-loop/contract")
def generation_loop_contract(request: Request) -> dict[str, Any]:
    payload = _generation_loop_contract_payload(request)
    payload["request_id"] = _request_id(request)
    return payload


@router.get("/models/onboarding/profile")
def onboarding_profile(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        payload = services.model_manager.recommend_onboarding_profile()
        payload["request_id"] = _request_id(request)
        return payload
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/models/onboarding/activation-plan")
def onboarding_activation_plan(
    request: Request,
    profile: str | None = None,
    include_remote_providers: bool = True,
    limit: int = 120,
    require_metadata: bool | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        payload = services.model_manager.onboarding_activation_plan(
            profile=profile,
            include_remote_providers=include_remote_providers,
            limit=max(1, min(limit, 500)),
            require_metadata=require_metadata,
        )
        payload["request_id"] = _request_id(request)
        return payload
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/models/onboarding/activate")
def onboarding_activate(payload: OnboardingActivateRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        result = services.model_manager.onboarding_activate(
            profile=payload.profile,
            include_remote_providers=bool(payload.include_remote_providers),
            limit=max(1, min(payload.limit, 500)),
            require_metadata=payload.require_metadata,
            activate=bool(payload.activate),
            run_smoke_test=bool(payload.run_smoke_test),
            smoke_prompt=payload.smoke_prompt,
        )
        target_id = (
            str(result.get("selected_package_id", "")).strip()
            or str(((result.get("install") or {}).get("package_id") or "")).strip()
            or str(payload.profile or "").strip()
            or "onboarding"
        )
        status = "succeeded" if str(result.get("status", "")).strip() in {"activated", "activated_with_smoke_warning"} else "failed"
        result["action_receipt"] = _sign_action(
            request,
            action="model_onboarding_activate",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=target_id,
            status=status,
            details={
                "status": str(result.get("status", "")),
                "ready": bool(result.get("ready", False)),
                "blockers": [str(item) for item in result.get("blockers", []) if str(item).strip()],
            },
        )
        result["request_id"] = _request_id(request)
        return result
    except ValueError as exc:
        _sign_action(
            request,
            action="model_onboarding_activate",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=str(payload.profile or "").strip() or "onboarding",
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="model_onboarding_activate",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=str(payload.profile or "").strip() or "onboarding",
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/models/packages")
def model_package_catalog(
    request: Request,
    profile: str | None = None,
    include_remote_providers: bool = True,
    limit: int = 120,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        payload = services.model_manager.model_package_catalog(
            profile=profile,
            include_remote_providers=include_remote_providers,
            limit=max(1, min(limit, 500)),
        )
        payload["request_id"] = _request_id(request)
        return payload
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/models/packages/license-admission")
def model_package_license_admission(
    request: Request,
    package_id: str,
    require_metadata: bool | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        payload = services.model_manager.model_package_license_admission(
            package_id=package_id,
            require_metadata=require_metadata,
        )
        payload["request_id"] = _request_id(request)
        return payload
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/models/packages/install")
def install_model_package(payload: InstallModelPackageRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        result = services.model_manager.install_model_package(
            package_id=payload.package_id,
            activate=bool(payload.activate),
        )
        target_id = str(result.get("model", "")).strip() or payload.package_id
        result["action_receipt"] = _sign_action(
            request,
            action="model_package_install",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=target_id,
        )
        result["request_id"] = _request_id(request)
        return result
    except ValueError as exc:
        _sign_action(
            request,
            action="model_package_install",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.package_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="model_package_install",
            payload=payload.model_dump(),
            actor=auth.user_id,
            target_id=payload.package_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


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


@router.post("/models/artifacts/admit", response_model=ModelArtifactAdmissionResponse)
def admit_model_artifact(
    payload: ModelArtifactAdmissionRequest,
    request: Request,
) -> ModelArtifactAdmissionResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        decision = services.model_manager.admit_model_artifact(
            manifest=payload.manifest,
            strict=bool(payload.strict),
            artifact_root=payload.artifact_root,
        )
        admitted = bool(decision.get("admitted"))
        model_id = str(decision.get("summary", {}).get("model_id") or "").strip() or None
        action_receipt = _sign_action(
            request,
            action="model_artifact_admission",
            payload={
                "strict": bool(payload.strict),
                "artifact_root": payload.artifact_root,
                "manifest": payload.manifest,
            },
            actor=auth.user_id,
            status="succeeded" if admitted else "failed",
            target_id=model_id,
            details={
                "admitted": admitted,
                "checks_failed": int(decision.get("summary", {}).get("checks_failed", 0)),
            },
        )
        return ModelArtifactAdmissionResponse(
            request_id=_request_id(request),
            admitted=admitted,
            mode=str(decision.get("mode") or ("strict" if payload.strict else "advisory")),
            errors=[str(item) for item in decision.get("errors", [])],
            warnings=[str(item) for item in decision.get("warnings", [])],
            summary=(
                dict(decision.get("summary"))
                if isinstance(decision.get("summary"), dict)
                else {}
            ),
            action_receipt=action_receipt,
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
