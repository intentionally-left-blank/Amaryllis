from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExecuteRequest(BaseModel):
    module: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    input: dict[str, Any] = Field(default_factory=dict)
    user_id: str = Field(min_length=1)
    session_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class ModuleResources(BaseModel):
    timeout_ms: int = Field(gt=0)
    memory_mb: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class ModuleManifest(BaseModel):
    name: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    runtime_api: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    permissions: list[str] = Field(default_factory=list)
    resources: ModuleResources

    model_config = ConfigDict(extra="forbid")


class ModuleExecutionResult(BaseModel):
    output: dict[str, Any]
    memory_write: dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class Context(BaseModel):
    request_id: str
    user_id: str = Field(min_length=1)
    session_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("request_id")
    @classmethod
    def validate_request_id_uuid4(cls, value: str) -> str:
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise ValueError("request_id must be a valid UUID4 string.") from exc

        if parsed.version != 4:
            raise ValueError("request_id must be UUID4.")

        return value


class ExecuteResponse(BaseModel):
    request_id: str
    module: str
    output: dict[str, Any]
    memory_write: dict[str, Any]
    execution_time_ms: int = Field(ge=0)

    model_config = ConfigDict(extra="forbid")


class ErrorDetail(BaseModel):
    type: str
    message: str
    request_id: str

    model_config = ConfigDict(extra="forbid")


class ErrorResponse(BaseModel):
    error: ErrorDetail

    model_config = ConfigDict(extra="forbid")
