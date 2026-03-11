from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from agents.agent_manager import AgentManager
from api.agent_api import router as agent_router
from api.chat_api import router as chat_router
from api.memory_api import router as memory_router
from api.model_api import router as model_router
from controller.meta_controller import MetaController
from memory.episodic_memory import EpisodicMemory
from memory.memory_manager import MemoryManager
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory
from memory.working_memory import WorkingMemory
from models.model_manager import ModelManager
from planner.planner import Planner
from runtime.config import AppConfig
from runtime.errors import AmaryllisError, InternalError
from runtime.telemetry import LocalTelemetry
from storage.database import Database
from storage.vector_store import VectorStore
from tasks.task_executor import TaskExecutor
from tools.tool_executor import ToolExecutor
from tools.tool_registry import ToolRegistry


@dataclass
class ServiceContainer:
    config: AppConfig
    database: Database
    vector_store: VectorStore
    model_manager: ModelManager
    memory_manager: MemoryManager
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    meta_controller: MetaController
    planner: Planner
    task_executor: TaskExecutor
    agent_manager: AgentManager
    telemetry: LocalTelemetry


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("amaryllis.runtime")


def create_services() -> ServiceContainer:
    config = AppConfig.from_env()
    config.ensure_directories()

    database = Database(config.database_path)
    vector_store = VectorStore(config.vector_index_path)
    telemetry = LocalTelemetry(config.telemetry_path)

    episodic = EpisodicMemory(database)
    semantic = SemanticMemory(database, vector_store)
    user_memory = UserMemory(database)
    working_memory = WorkingMemory(database)
    memory_manager = MemoryManager(
        episodic=episodic,
        semantic=semantic,
        user_memory=user_memory,
        working_memory=working_memory,
        telemetry=telemetry,
    )

    tool_registry = ToolRegistry()
    tool_registry.load_builtin_tools()
    tool_registry.discover_plugins(config.plugins_dir)

    tool_executor = ToolExecutor(tool_registry)

    model_manager = ModelManager(config=config, database=database)

    meta_controller = MetaController()
    planner = Planner()
    task_executor = TaskExecutor(
        model_manager=model_manager,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        meta_controller=meta_controller,
        planner=planner,
    )

    agent_manager = AgentManager(database=database, task_executor=task_executor)

    return ServiceContainer(
        config=config,
        database=database,
        vector_store=vector_store,
        model_manager=model_manager,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        meta_controller=meta_controller,
        planner=planner,
        task_executor=task_executor,
        agent_manager=agent_manager,
        telemetry=telemetry,
    )


def create_app() -> FastAPI:
    services = create_services()

    app = FastAPI(
        title="Amaryllis Runtime",
        version="0.1.0",
        description="Local AI brain node runtime for macOS.",
    )
    app.state.services = services
    services.telemetry.emit(
        "runtime_start",
        {
            "app": services.config.app_name,
            "host": services.config.host,
            "port": services.config.port,
        },
    )

    def request_id_from_request(request: Request) -> str:
        request_id = getattr(request.state, "request_id", None)
        if isinstance(request_id, str) and request_id.strip():
            return request_id
        generated = str(uuid4())
        request.state.request_id = generated
        return generated

    def error_response(
        request: Request,
        *,
        status_code: int,
        error_type: str,
        message: str,
    ) -> JSONResponse:
        request_id = request_id_from_request(request)
        services.telemetry.emit(
            "request_error",
            {
                "request_id": request_id,
                "error_type": error_type,
                "message": message,
                "status_code": status_code,
                "path": request.url.path,
                "method": request.method,
            },
        )
        logger.error(
            "request_failed request_id=%s type=%s status=%s path=%s message=%s",
            request_id,
            error_type,
            status_code,
            request.url.path,
            message,
        )
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "type": error_type,
                    "message": message,
                    "request_id": request_id,
                }
            },
            headers={"X-Request-ID": request_id},
        )

    @app.middleware("http")
    async def request_trace_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "").strip() or str(uuid4())
        request.state.request_id = request_id

        start = time.perf_counter()
        services.telemetry.emit(
            "request_start",
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        logger.info(
            "request_start request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
        response.headers["X-Request-ID"] = request_id
        services.telemetry.emit(
            "request_done",
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        logger.info(
            "request_done request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.exception_handler(AmaryllisError)
    async def handle_amaryllis_error(request: Request, exc: AmaryllisError):
        return error_response(
            request,
            status_code=exc.status_code,
            error_type=exc.error_type,
            message=exc.message,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(request: Request, exc: RequestValidationError):
        messages = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", []))
            msg = str(err.get("msg", "Invalid request"))
            messages.append(f"{loc}: {msg}" if loc else msg)
        message = "; ".join(messages) if messages else "Invalid request payload"
        return error_response(
            request,
            status_code=422,
            error_type="validation_error",
            message=message,
        )

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException):
        message = str(exc.detail) if exc.detail is not None else "HTTP error"
        return error_response(
            request,
            status_code=exc.status_code,
            error_type="http_error",
            message=message,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(request: Request, exc: Exception):
        logger.exception("unhandled_exception path=%s error=%s", request.url.path, exc)
        internal = InternalError()
        return error_response(
            request,
            status_code=internal.status_code,
            error_type=internal.error_type,
            message=internal.message,
        )

    app.include_router(chat_router)
    app.include_router(model_router)
    app.include_router(agent_router)
    app.include_router(memory_router)

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "app": services.config.app_name,
            "active_provider": services.model_manager.active_provider,
            "active_model": services.model_manager.active_model,
            "request_id": request_id_from_request(request),
        }

    @app.get("/health/providers")
    def health_providers(request: Request) -> dict[str, Any]:
        checks = services.model_manager.provider_health()
        overall_status = "ok"
        if any(item.get("status") == "error" for item in checks.values()):
            overall_status = "degraded"

        return {
            "status": overall_status,
            "request_id": request_id_from_request(request),
            "active_provider": services.model_manager.active_provider,
            "active_model": services.model_manager.active_model,
            "providers": checks,
        }

    @app.on_event("shutdown")
    def shutdown_event() -> None:
        logger.info("shutdown_start")
        services.telemetry.emit(
            "runtime_shutdown_start",
            {
                "app": services.config.app_name,
            },
        )
        services.database.close()
        services.vector_store.persist()
        services.telemetry.emit(
            "runtime_shutdown_done",
            {
                "app": services.config.app_name,
            },
        )
        logger.info("shutdown_done")

    return app


app = create_app()
