from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from runtime.auth import auth_context_from_request
from runtime.privacy_transparency import build_privacy_transparency_contract

router = APIRouter(tags=["privacy"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _contract_payload(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    payload = build_privacy_transparency_contract(
        config=services.config,
        model_manager=services.model_manager,
        observability=services.observability,
    )
    payload["request_id"] = _request_id(request)
    return payload


@router.get("/privacy/transparency")
def privacy_transparency(request: Request) -> dict[str, Any]:
    return _contract_payload(request)


@router.get("/service/privacy/transparency")
def service_privacy_transparency(request: Request) -> dict[str, Any]:
    auth = auth_context_from_request(request)
    payload = _contract_payload(request)
    payload["actor"] = auth.user_id
    payload["scopes"] = sorted(auth.scopes)
    return payload
