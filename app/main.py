from __future__ import annotations

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import AmaryllisError, ValidationError
from app.models import ErrorResponse, ExecuteRequest, ExecuteResponse
from app.runtime import RuntimeService


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Amaryllis Runtime", version="0.3.0")
runtime_service = RuntimeService()
logger = logging.getLogger("amaryllis.api")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(AmaryllisError)
async def amaryllis_error_handler(
    request: Request, exc: AmaryllisError
) -> JSONResponse:
    logger.error(
        "request_failed request_id=%s error_type=%s message=%s",
        exc.request_id,
        exc.error_type,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump(),
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    error = ValidationError(
        message=f"Request validation failed: {exc.errors()}",
        request_id=request_id,
    )
    logger.error(
        "request_failed request_id=%s error_type=%s message=%s",
        request_id,
        error.error_type,
        error.message,
    )
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_response().model_dump(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    logger.error(
        "request_failed request_id=%s error_type=UnhandledException message=%s",
        request_id,
        str(exc),
        exc_info=True,
    )
    error_response = ErrorResponse(
        error={
            "type": "AmaryllisError",
            "message": "Internal server error.",
            "request_id": request_id,
        }
    )
    return JSONResponse(status_code=500, content=error_response.model_dump())


@app.post("/execute", response_model=ExecuteResponse)
def execute(payload: ExecuteRequest, request: Request) -> ExecuteResponse:
    request_id = request.state.request_id
    return runtime_service.execute(payload, request_id=request_id)
