from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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


@router.post("/models/download")
def download_model(payload: DownloadModelRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.model_manager.download_model(
            model_id=payload.model_id,
            provider=payload.provider,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/models/load")
def load_model(payload: LoadModelRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.model_manager.load_model(
            model_id=payload.model_id,
            provider=payload.provider,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
