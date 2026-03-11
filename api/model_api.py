from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from runtime.errors import ProviderError, ValidationError

router = APIRouter(tags=["models"])


class DownloadModelRequest(BaseModel):
    model_id: str = Field(min_length=1)
    provider: str | None = None


class LoadModelRequest(BaseModel):
    model_id: str = Field(min_length=1)
    provider: str | None = None


@router.get("/models")
def list_models(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    return services.model_manager.list_models()


@router.get("/models/capabilities")
def model_capabilities(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    return {
        "active": {
            "provider": services.model_manager.active_provider,
            "model": services.model_manager.active_model,
        },
        "providers": services.model_manager.provider_capabilities(),
    }


@router.post("/models/download")
def download_model(payload: DownloadModelRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.model_manager.download_model(
            model_id=payload.model_id,
            provider=payload.provider,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/models/load")
def load_model(payload: LoadModelRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.model_manager.load_model(
            model_id=payload.model_id,
            provider=payload.provider,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc
