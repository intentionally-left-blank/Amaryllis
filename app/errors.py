from __future__ import annotations

from app.models import ErrorDetail, ErrorResponse


class AmaryllisError(Exception):
    error_type = "AmaryllisError"
    status_code = 500

    def __init__(self, message: str, request_id: str) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorDetail(
                type=self.error_type,
                message=self.message,
                request_id=self.request_id,
            )
        )


class ModuleLoadError(AmaryllisError):
    error_type = "ModuleLoadError"
    status_code = 400


class ModuleExecutionError(AmaryllisError):
    error_type = "ModuleExecutionError"
    status_code = 500


class ValidationError(AmaryllisError):
    error_type = "ValidationError"
    status_code = 422
