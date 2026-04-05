from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EndpointContract:
    path: str
    method: str
    request_required: tuple[str, ...] = ()
    response_statuses: tuple[str, ...] = ("200",)


CORE_V1_CONTRACT: tuple[EndpointContract, ...] = (
    EndpointContract(
        path="/v1/chat/completions",
        method="post",
        request_required=("messages",),
        response_statuses=("200", "422"),
    ),
    EndpointContract(path="/v1/models", method="get", response_statuses=("200",)),
    EndpointContract(path="/v1/models/download", method="post", request_required=("model_id",)),
    EndpointContract(path="/v1/models/load", method="post", request_required=("model_id",)),
    EndpointContract(path="/v1/agents/create", method="post", request_required=("name", "system_prompt")),
    EndpointContract(path="/v1/agents/factory/contract", method="get", response_statuses=("200",)),
    EndpointContract(path="/v1/agents/quickstart", method="post", request_required=("request",)),
    EndpointContract(path="/v1/agents/quickstart/plan", method="post", request_required=("request",)),
    EndpointContract(path="/v1/agents", method="get", response_statuses=("200",)),
    EndpointContract(path="/v1/automations", method="get", response_statuses=("200",)),
    EndpointContract(path="/v1/tools", method="get", response_statuses=("200",)),
    EndpointContract(path="/v1/inbox", method="get", response_statuses=("200",)),
)


def load_contract(path: Path) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Compatibility contract must be a JSON object.")
    return payload


def save_contract(path: Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_contract_payload(
    *,
    contract_version: str,
    endpoints: tuple[EndpointContract, ...] = CORE_V1_CONTRACT,
) -> dict[str, Any]:
    return {
        "contract_version": str(contract_version),
        "endpoints": [
            {
                "path": item.path,
                "method": item.method,
                "request_required": list(item.request_required),
                "response_statuses": list(item.response_statuses),
            }
            for item in endpoints
        ],
    }


def validate_openapi_contract(*, openapi: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        return ["OpenAPI schema does not contain 'paths' object."]
    entries = contract.get("endpoints")
    if not isinstance(entries, list):
        return ["Compatibility contract has invalid 'endpoints' section."]

    for raw in entries:
        if not isinstance(raw, dict):
            errors.append("Compatibility contract endpoint entry is not an object.")
            continue
        path = str(raw.get("path") or "").strip()
        method = str(raw.get("method") or "").strip().lower()
        required_fields = [str(item).strip() for item in raw.get("request_required", []) if str(item).strip()]
        statuses = [str(item).strip() for item in raw.get("response_statuses", []) if str(item).strip()]
        if not path or not method:
            errors.append("Compatibility contract endpoint entry is missing path or method.")
            continue

        path_item = paths.get(path)
        if not isinstance(path_item, dict):
            errors.append(f"Missing path in OpenAPI schema: {path}")
            continue
        operation = path_item.get(method)
        if not isinstance(operation, dict):
            errors.append(f"Missing method in OpenAPI schema: {method.upper()} {path}")
            continue

        if required_fields:
            operation_required = _extract_required_request_fields(openapi=openapi, operation=operation)
            missing_fields = [field for field in required_fields if field not in operation_required]
            if missing_fields:
                joined = ", ".join(sorted(missing_fields))
                errors.append(f"Missing required request fields for {method.upper()} {path}: {joined}")

        if statuses:
            response_map = operation.get("responses")
            if not isinstance(response_map, dict):
                errors.append(f"Missing responses map for {method.upper()} {path}")
            else:
                missing_statuses = [code for code in statuses if code not in response_map]
                if missing_statuses:
                    joined = ", ".join(sorted(missing_statuses))
                    errors.append(f"Missing response statuses for {method.upper()} {path}: {joined}")

    return errors


def _extract_required_request_fields(*, openapi: dict[str, Any], operation: dict[str, Any]) -> set[str]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return set()
    content = request_body.get("content")
    if not isinstance(content, dict):
        return set()
    payload_schema: dict[str, Any] | None = None
    for content_type in ("application/json", "application/*+json"):
        candidate = content.get(content_type)
        if isinstance(candidate, dict):
            schema = candidate.get("schema")
            if isinstance(schema, dict):
                payload_schema = schema
                break
    if payload_schema is None:
        for item in content.values():
            if not isinstance(item, dict):
                continue
            schema = item.get("schema")
            if isinstance(schema, dict):
                payload_schema = schema
                break
    if payload_schema is None:
        return set()

    resolved = _resolve_schema(openapi=openapi, schema=payload_schema)
    required = resolved.get("required")
    if not isinstance(required, list):
        return set()
    return {str(item).strip() for item in required if str(item).strip()}


def _resolve_schema(*, openapi: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in schema:
        return schema
    ref = str(schema.get("$ref") or "").strip()
    if not ref.startswith("#/components/schemas/"):
        return schema
    name = ref.rsplit("/", 1)[-1]
    components = openapi.get("components")
    if not isinstance(components, dict):
        return schema
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return schema
    target = schemas.get(name)
    if not isinstance(target, dict):
        return schema
    if target is schema:
        return schema
    return _resolve_schema(openapi=openapi, schema=target)
