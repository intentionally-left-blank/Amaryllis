from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AmaryllisError(Exception):
    message: str
    error_type: str = "amaryllis_error"
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


class ValidationError(AmaryllisError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_type="validation_error", status_code=400)


class NotFoundError(AmaryllisError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_type="not_found", status_code=404)


class ProviderError(AmaryllisError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_type="provider_error", status_code=502)


class InternalError(AmaryllisError):
    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(message=message, error_type="internal_error", status_code=500)
