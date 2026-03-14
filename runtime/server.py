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
from agents.agent_run_manager import AgentRunManager
from api.agent_api import router as agent_router
from api.automation_api import router as automation_router
from api.chat_api import router as chat_router
from api.inbox_api import router as inbox_router
from api.memory_api import router as memory_router
from api.model_api import router as model_router
from api.security_api import router as security_router
from api.tool_api import router as tool_router
from automation.automation_scheduler import AutomationScheduler
from controller.meta_controller import MetaController
from memory.episodic_memory import EpisodicMemory
from memory.consolidation_worker import MemoryConsolidationWorker
from memory.memory_manager import MemoryManager
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory
from memory.working_memory import WorkingMemory
from models.model_manager import ModelManager
from planner.planner import Planner
from runtime.auth import AuthContext, AuthManager, auth_context_from_request
from runtime.config import AppConfig
from runtime.errors import AmaryllisError, InternalError, PermissionDeniedError
from runtime.security import LocalIdentityManager, SecurityManager
from runtime.telemetry import LocalTelemetry
from storage.database import Database
from storage.vector_store import VectorStore
from tasks.task_executor import TaskExecutor
from tools.mcp_client_registry import MCPClientRegistry
from tools.permission_manager import ToolPermissionManager
from tools.policy import ToolIsolationPolicy
from tools.tool_budget import ToolBudgetGuard
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
    agent_run_manager: AgentRunManager
    agent_manager: AgentManager
    automation_scheduler: AutomationScheduler
    memory_consolidation_worker: MemoryConsolidationWorker | None
    mcp_registry: MCPClientRegistry | None
    telemetry: LocalTelemetry
    identity_manager: LocalIdentityManager
    security_manager: SecurityManager
    auth_manager: AuthManager


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
    identity_manager = LocalIdentityManager(config.identity_path)
    security_manager = SecurityManager(
        identity_manager=identity_manager,
        database=database,
        telemetry=telemetry,
    )
    auth_manager = AuthManager(
        enabled=config.auth_enabled,
        token_specs=config.auth_tokens,
    )

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
        profile_decay_enabled=config.memory_profile_decay_enabled,
        profile_decay_half_life_days=config.memory_profile_decay_half_life_days,
        profile_decay_floor=config.memory_profile_decay_floor,
        profile_decay_min_delta=config.memory_profile_decay_min_delta,
    )

    tool_registry = ToolRegistry(
        plugin_signing_key=config.plugin_signing_key,
        plugin_signing_mode=config.plugin_signing_mode,
    )
    tool_registry.load_builtin_tools()
    tool_registry.discover_plugins(config.plugins_dir)
    mcp_registry: MCPClientRegistry | None = None
    if config.mcp_endpoints:
        mcp_registry = MCPClientRegistry(
            endpoints=list(config.mcp_endpoints),
            timeout_sec=config.mcp_timeout_sec,
            failure_threshold=config.mcp_failure_threshold,
            quarantine_sec=config.mcp_quarantine_sec,
        )
        discovered = mcp_registry.register_remote_tools(tool_registry)
        logger.info("mcp_tools_discovered count=%s", discovered)

    tool_permission_manager = ToolPermissionManager()
    tool_budget_guard = ToolBudgetGuard(
        window_sec=config.tool_budget_window_sec,
        max_calls_per_tool=config.tool_budget_max_calls_per_tool,
        max_total_calls=config.tool_budget_max_total_calls,
        max_high_risk_calls=config.tool_budget_max_high_risk_calls,
    )
    tool_policy = ToolIsolationPolicy(
        blocked_tools=list(config.blocked_tools),
        profile=config.tool_isolation_profile,
        allowed_high_risk_tools=list(config.allowed_high_risk_tools),
        python_exec_max_timeout_sec=config.tool_python_exec_max_timeout_sec,
        python_exec_max_code_chars=config.tool_python_exec_max_code_chars,
        filesystem_allow_write=config.tool_filesystem_allow_write,
    )
    tool_executor = ToolExecutor(
        tool_registry,
        policy=tool_policy,
        permission_manager=tool_permission_manager,
        budget_guard=tool_budget_guard,
        approval_enforcement_mode=config.tool_approval_enforcement,
        telemetry_emitter=telemetry.emit,
    )

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
        max_duration_sec=config.task_max_duration_sec,
        max_model_calls=config.task_max_model_calls,
        max_prompt_chars=config.task_max_prompt_chars,
        max_tool_rounds=config.task_max_tool_rounds,
        issue_parallel_workers=config.task_issue_parallel_workers,
        issue_timeout_sec=config.task_issue_timeout_sec,
        verifier_enabled=config.task_verifier_enabled,
        verifier_max_repair_attempts=config.task_verifier_max_repair_attempts,
        verifier_min_response_chars=config.task_verifier_min_response_chars,
        artifact_quality_enabled=config.task_artifact_quality_enabled,
        artifact_quality_max_repair_attempts=config.task_artifact_quality_max_repair_attempts,
        step_verifier_enabled=config.task_step_verifier_enabled,
        step_max_retries_default=config.task_step_max_retries_default,
        step_replan_max_attempts=config.task_step_replan_max_attempts,
    )

    agent_run_manager = AgentRunManager(
        database=database,
        task_executor=task_executor,
        worker_count=config.run_workers,
        default_max_attempts=config.run_max_attempts,
        attempt_timeout_sec=config.run_attempt_timeout_sec,
        run_lease_ttl_sec=config.run_lease_ttl_sec,
        retry_backoff_sec=config.run_retry_backoff_sec,
        retry_max_backoff_sec=config.run_retry_max_backoff_sec,
        retry_jitter_sec=config.run_retry_jitter_sec,
        run_budget_max_tokens=config.run_budget_max_tokens,
        run_budget_max_duration_sec=config.run_budget_max_duration_sec,
        run_budget_max_tool_calls=config.run_budget_max_tool_calls,
        run_budget_max_tool_errors=config.run_budget_max_tool_errors,
        telemetry=telemetry,
    )
    agent_run_manager.start()
    agent_manager = AgentManager(
        database=database,
        task_executor=task_executor,
        run_manager=agent_run_manager,
    )
    automation_scheduler = AutomationScheduler(
        database=database,
        run_manager=agent_run_manager,
        poll_interval_sec=config.automation_poll_sec,
        batch_size=config.automation_batch_size,
        escalation_warning_threshold=config.automation_escalation_warning,
        escalation_critical_threshold=config.automation_escalation_critical,
        escalation_disable_threshold=config.automation_escalation_disable,
        lease_ttl_sec=config.automation_lease_ttl_sec,
        backoff_base_sec=config.automation_backoff_base_sec,
        backoff_max_sec=config.automation_backoff_max_sec,
        circuit_failure_threshold=config.automation_circuit_failure_threshold,
        circuit_open_sec=config.automation_circuit_open_sec,
        telemetry=telemetry,
    )
    automation_scheduler.start()

    memory_consolidation_worker: MemoryConsolidationWorker | None = None
    if config.memory_consolidation_enabled:
        memory_consolidation_worker = MemoryConsolidationWorker(
            database=database,
            memory_manager=memory_manager,
            interval_sec=config.memory_consolidation_interval_sec,
            semantic_limit=config.memory_consolidation_semantic_limit,
            max_users_per_tick=config.memory_consolidation_max_users_per_tick,
            telemetry=telemetry,
        )
        memory_consolidation_worker.start()

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
        agent_run_manager=agent_run_manager,
        agent_manager=agent_manager,
        automation_scheduler=automation_scheduler,
        memory_consolidation_worker=memory_consolidation_worker,
        mcp_registry=mcp_registry,
        telemetry=telemetry,
        identity_manager=identity_manager,
        security_manager=security_manager,
        auth_manager=auth_manager,
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
        if error_type in {"authentication_error", "permission_denied"}:
            actor: str | None = None
            scopes: list[str] = []
            auth_context = getattr(request.state, "auth_context", None)
            if isinstance(auth_context, AuthContext):
                actor = auth_context.user_id
                scopes = sorted(auth_context.scopes)
            try:
                services.security_manager.audit_access_denied(
                    denial_type=error_type,
                    request_id=request_id,
                    actor=actor,
                    path=request.url.path,
                    method=request.method,
                    message=message,
                    status_code=status_code,
                    scopes=scopes,
                )
            except Exception as exc:
                logger.warning(
                    "security_access_audit_failed request_id=%s error=%s",
                    request_id,
                    exc,
                )
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
        path = request.url.path

        is_public = path == "/health"
        if not is_public:
            auth_context = services.auth_manager.authenticate_request(request)
            request.state.auth_context = auth_context
            if path.startswith("/security/") or path.startswith("/debug/"):
                if not auth_context.is_admin:
                    raise PermissionDeniedError("Admin scope is required")
            elif path.startswith("/service/"):
                if not auth_context.has_any_scope("service", "admin"):
                    raise PermissionDeniedError("Service scope is required")
            elif not auth_context.has_any_scope("user", "admin"):
                raise PermissionDeniedError("User scope is required")

        start = time.perf_counter()
        services.telemetry.emit(
            "request_start",
            {
                "request_id": request_id,
                "method": request.method,
                "path": path,
            },
        )
        logger.info(
            "request_start request_id=%s method=%s path=%s",
            request_id,
            request.method,
            path,
        )

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
        response.headers["X-Request-ID"] = request_id
        services.telemetry.emit(
            "request_done",
            {
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        logger.info(
            "request_done request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
            request_id,
            request.method,
            path,
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
    app.include_router(automation_router)
    app.include_router(inbox_router)
    app.include_router(memory_router)
    app.include_router(tool_router)
    app.include_router(security_router)

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

    @app.get("/service/health")
    def service_health(request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        checks = services.model_manager.provider_health()
        return {
            "status": "ok",
            "request_id": request_id_from_request(request),
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
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
        services.automation_scheduler.stop()
        if services.memory_consolidation_worker is not None:
            services.memory_consolidation_worker.stop()
        services.agent_run_manager.stop()
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
