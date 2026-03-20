from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from agents.agent_manager import AgentManager
from agents.agent_run_manager import AgentRunManager
from api.agent_api import router as agent_router
from api.automation_api import router as automation_router
from api.backup_api import router as backup_router
from api.chat_api import router as chat_router
from api.inbox_api import router as inbox_router
from api.memory_api import router as memory_router
from api.model_api import router as model_router
from api.security_api import router as security_router
from api.tool_api import router as tool_router
from api.voice_api import router as voice_router
from automation.automation_scheduler import AutomationScheduler
from controller.meta_controller import MetaController
from kernel.orchestration import KernelExecutorAdapter
from memory.episodic_memory import EpisodicMemory
from memory.consolidation_worker import MemoryConsolidationWorker
from memory.memory_manager import MemoryManager
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory
from memory.working_memory import WorkingMemory
from models.model_manager import ModelManager
from planner.planner import Planner
from runtime.backup import BackupManager, BackupScheduler
from runtime.compliance import ComplianceManager
from runtime.api_lifecycle import APILifecyclePolicy, canonical_api_path
from runtime.auth import AuthContext, AuthManager, auth_context_from_request
from runtime.config import AppConfig
from runtime.errors import AmaryllisError, InternalError, PermissionDeniedError, ProviderError, ValidationError
from runtime.observability import ObservabilityManager, ObservabilityTelemetry, SLOTargets
from runtime.security import LocalIdentityManager, SecurityManager
from runtime.telemetry import LocalTelemetry
from storage.database import Database
from storage.vector_store import VectorStore
from tasks.task_executor import TaskExecutor
from tools.mcp_client_registry import MCPClientRegistry
from tools.autonomy_policy import AutonomyPolicy
from tools.browser_action_adapter import BrowserActionAdapter, StubBrowserActionAdapter, register_browser_action_tool
from tools.permission_manager import ToolPermissionManager
from tools.policy import ToolIsolationPolicy
from tools.sandbox_runner import ToolSandboxConfig, ToolSandboxRunner
from tools.tool_budget import ToolBudgetGuard
from tools.tool_executor import ToolExecutor
from tools.tool_registry import ToolRegistry
from voice.session_manager import VoiceSessionManager
from voice.stt_adapter import STTAdapter, create_stt_adapter_from_env


@dataclass
class ServiceContainer:
    config: AppConfig
    database: Database
    vector_store: VectorStore
    model_manager: ModelManager
    memory_manager: MemoryManager
    tool_registry: ToolRegistry
    browser_adapter: BrowserActionAdapter
    voice_session_manager: VoiceSessionManager
    stt_adapter: STTAdapter
    tool_executor: ToolExecutor
    meta_controller: MetaController
    planner: Planner
    task_executor: TaskExecutor
    agent_run_manager: AgentRunManager
    agent_manager: AgentManager
    automation_scheduler: AutomationScheduler
    memory_consolidation_worker: MemoryConsolidationWorker | None
    backup_manager: BackupManager
    backup_scheduler: BackupScheduler | None
    mcp_registry: MCPClientRegistry | None
    telemetry: Any
    local_telemetry: LocalTelemetry
    observability: ObservabilityManager
    api_lifecycle: APILifecyclePolicy
    identity_manager: LocalIdentityManager
    security_manager: SecurityManager
    compliance_manager: ComplianceManager
    auth_manager: AuthManager


class RunKillSwitchRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)
    include_running: bool = True
    include_queued: bool = True
    limit: int = Field(default=5000, ge=1, le=50000)


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
    local_telemetry = LocalTelemetry(config.telemetry_path)
    observability = ObservabilityManager(
        logger=logger,
        service_name=config.app_name,
        service_version=config.app_version,
        environment=config.security_profile,
        otel_enabled=config.observability_otel_enabled,
        otlp_endpoint=config.observability_otlp_endpoint,
        slo_targets=SLOTargets(
            window_sec=config.observability_slo_window_sec,
            request_availability_target=config.observability_request_availability_target,
            request_latency_p95_ms_target=config.observability_request_latency_p95_ms_target,
            run_success_target=config.observability_run_success_target,
            min_request_samples=config.observability_min_request_samples,
            min_run_samples=config.observability_min_run_samples,
            incident_cooldown_sec=config.observability_incident_cooldown_sec,
        ),
    )
    telemetry = ObservabilityTelemetry(base=local_telemetry, monitor=observability.sre)
    api_lifecycle = APILifecyclePolicy(
        version=config.api_version,
        release_channel=config.api_release_channel,
        deprecation_sunset_days=config.api_deprecation_sunset_days,
    )
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
    compliance_manager = ComplianceManager(
        config=config,
        database=database,
        security_manager=security_manager,
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
        plugin_runtime_mode=config.plugin_runtime_mode,
    )
    tool_registry.load_builtin_tools()
    browser_adapter: BrowserActionAdapter = StubBrowserActionAdapter()
    register_browser_action_tool(tool_registry, browser_adapter)
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
        autonomy_policy=AutonomyPolicy(level=config.autonomy_level),
        sandbox_runner=(
            ToolSandboxRunner(
                config=ToolSandboxConfig(
                    timeout_sec=config.tool_sandbox_timeout_sec,
                    max_cpu_sec=config.tool_sandbox_max_cpu_sec,
                    max_memory_mb=config.tool_sandbox_max_memory_mb,
                    allow_network_tools=config.tool_sandbox_allow_network_tools,
                    allowed_roots=config.tool_sandbox_allowed_roots,
                    filesystem_allow_write=config.tool_filesystem_allow_write,
                    max_python_code_chars=config.tool_python_exec_max_code_chars,
                )
            )
            if config.tool_sandbox_enabled
            else None
        ),
        telemetry_emitter=telemetry.emit,
    )
    voice_session_manager = VoiceSessionManager(telemetry_emitter=telemetry.emit)
    stt_adapter = create_stt_adapter_from_env()

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
    kernel_executor = KernelExecutorAdapter(task_executor)

    agent_run_manager = AgentRunManager(
        database=database,
        task_executor=kernel_executor,
        worker_count=config.run_workers,
        recover_pending_on_start=config.run_recover_pending_on_start,
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
        task_executor=kernel_executor,
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
    if config.automation_enabled:
        automation_scheduler.start()
    else:
        logger.info("automation_scheduler_disabled")

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

    backup_manager = BackupManager(
        database=database,
        vector_store=vector_store,
        data_dir=config.data_dir,
        backup_dir=config.backup_dir,
        database_path=config.database_path,
        identity_path=config.identity_path,
        app_version=config.app_version,
        retention_count=config.backup_retention_count,
        retention_days=config.backup_retention_days,
        verify_on_create=config.backup_verify_on_create,
        telemetry=telemetry,
    )
    backup_scheduler: BackupScheduler | None = None
    if config.backup_enabled:
        backup_scheduler = BackupScheduler(
            manager=backup_manager,
            interval_sec=config.backup_interval_sec,
            restore_drill_enabled=config.backup_restore_drill_enabled,
            restore_drill_interval_sec=config.backup_restore_drill_interval_sec,
            telemetry=telemetry,
        )
        backup_scheduler.start()

    return ServiceContainer(
        config=config,
        database=database,
        vector_store=vector_store,
        model_manager=model_manager,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        browser_adapter=browser_adapter,
        voice_session_manager=voice_session_manager,
        stt_adapter=stt_adapter,
        tool_executor=tool_executor,
        meta_controller=meta_controller,
        planner=planner,
        task_executor=task_executor,
        agent_run_manager=agent_run_manager,
        agent_manager=agent_manager,
        automation_scheduler=automation_scheduler,
        memory_consolidation_worker=memory_consolidation_worker,
        backup_manager=backup_manager,
        backup_scheduler=backup_scheduler,
        mcp_registry=mcp_registry,
        telemetry=telemetry,
        local_telemetry=local_telemetry,
        observability=observability,
        api_lifecycle=api_lifecycle,
        identity_manager=identity_manager,
        security_manager=security_manager,
        compliance_manager=compliance_manager,
        auth_manager=auth_manager,
    )


def create_app() -> FastAPI:
    services = create_services()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
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
            if services.backup_scheduler is not None:
                services.backup_scheduler.stop()
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

    app = FastAPI(
        title="Amaryllis Runtime",
        version=services.config.app_version,
        description="Local AI brain node runtime for macOS.",
        lifespan=lifespan,
    )
    app.state.services = services
    services.telemetry.emit(
        "runtime_start",
        {
            "app": services.config.app_name,
            "version": services.config.app_version,
            "host": services.config.host,
            "port": services.config.port,
            "api_version": services.config.api_version,
            "release_channel": services.config.api_release_channel,
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
        response = JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "type": error_type,
                    "message": message,
                    "request_id": request_id,
                }
            },
            headers={
                "X-Request-ID": request_id,
                "X-Trace-ID": str(getattr(request.state, "trace_id", request_id)),
            },
        )
        for key, value in services.api_lifecycle.response_headers(request.url.path).items():
            response.headers[key] = value
        return response

    @app.middleware("http")
    async def request_trace_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "").strip() or str(uuid4())
        request.state.request_id = request_id
        path = str(request.url.path)
        auth_path = canonical_api_path(path)
        request.state.api_path = auth_path
        span_ctx = services.observability.start_request_span(
            request_id=request_id,
            method=request.method,
            path=auth_path or path,
        )
        request.state.trace_id = span_ctx.trace_id or request_id
        start = time.perf_counter()
        services.telemetry.emit(
            "request_start",
            {
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "canonical_path": auth_path,
                "trace_id": str(getattr(request.state, "trace_id", "")),
            },
        )
        if services.config.request_trace_logs_enabled:
            logger.info(
                "request_start request_id=%s trace_id=%s method=%s path=%s canonical_path=%s",
                request_id,
                str(getattr(request.state, "trace_id", "")),
                request.method,
                path,
                auth_path,
            )

        response: JSONResponse | Any
        error_type: str | None = None
        try:
            is_public = auth_path == "/health"
            if not is_public:
                auth_context = services.auth_manager.authenticate_request(request)
                request.state.auth_context = auth_context
                if auth_path.startswith("/security/") or auth_path.startswith("/debug/"):
                    if not auth_context.is_admin:
                        raise PermissionDeniedError("Admin scope is required")
                elif auth_path.startswith("/service/"):
                    if not auth_context.has_any_scope("service", "admin"):
                        raise PermissionDeniedError("Service scope is required")
                elif not auth_context.has_any_scope("user", "admin"):
                    raise PermissionDeniedError("User scope is required")
                services.security_manager.record_authenticated_request(
                    auth_context=auth_context,
                    request_id=request_id,
                    path=auth_path or path,
                    method=request.method,
                    metadata={
                        "release_channel": services.config.api_release_channel,
                    },
                )
            response = await call_next(request)
        except AmaryllisError as exc:
            error_type = exc.error_type
            response = error_response(
                request,
                status_code=exc.status_code,
                error_type=exc.error_type,
                message=exc.message,
            )
        except Exception as exc:
            logger.exception("unhandled_exception path=%s error=%s", request.url.path, exc)
            internal = InternalError()
            error_type = internal.error_type
            response = error_response(
                request,
                status_code=internal.status_code,
                error_type=internal.error_type,
                message=internal.message,
            )

        duration_ms = round((time.perf_counter() - start) * 1000.0, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = str(getattr(request.state, "trace_id", request_id))
        for key, value in services.api_lifecycle.response_headers(path).items():
            response.headers[key] = value
        services.telemetry.emit(
            "request_done",
            {
                "request_id": request_id,
                "method": request.method,
                "path": path,
                "canonical_path": auth_path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "trace_id": str(getattr(request.state, "trace_id", "")),
            },
        )
        services.observability.finish_request_span(
            context=span_ctx,
            status_code=int(response.status_code),
            duration_ms=duration_ms,
            error_type=error_type,
        )
        if services.config.request_trace_logs_enabled:
            logger.info(
                "request_done request_id=%s trace_id=%s method=%s path=%s status=%s duration_ms=%.2f",
                request_id,
                str(getattr(request.state, "trace_id", "")),
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
    app.include_router(voice_router)
    app.include_router(backup_router)
    # Versioned API aliases for lifecycle-managed stable contract.
    app.include_router(model_router, prefix="/v1")
    app.include_router(agent_router, prefix="/v1")
    app.include_router(automation_router, prefix="/v1")
    app.include_router(inbox_router, prefix="/v1")
    app.include_router(memory_router, prefix="/v1")
    app.include_router(tool_router, prefix="/v1")
    app.include_router(voice_router, prefix="/v1")
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
            "autonomy_level": services.config.autonomy_level,
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
            "autonomy_level": services.config.autonomy_level,
            "providers": checks,
        }

    @app.get("/service/observability/slo")
    def service_observability_slo(request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        return {
            "request_id": request_id_from_request(request),
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "profiles": {
                "runtime": services.config.runtime_profile,
                "slo": services.config.slo_profile,
            },
            "quality_budget": {
                "request_burn_rate": services.config.slo_budget_request_burn_rate,
                "run_burn_rate": services.config.slo_budget_run_burn_rate,
                "perf_max_p95_latency_ms": services.config.perf_budget_max_p95_latency_ms,
                "perf_max_error_rate_pct": services.config.perf_budget_max_error_rate_pct,
            },
            "snapshot": services.observability.sre.snapshot(),
        }

    @app.get("/service/observability/incidents")
    def service_observability_incidents(request: Request, limit: int = 100) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        return {
            "request_id": request_id_from_request(request),
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "items": services.observability.sre.list_incidents(limit=max(1, min(limit, 1000))),
        }

    @app.get("/service/observability/metrics")
    def service_observability_metrics(request: Request) -> PlainTextResponse:
        _ = auth_context_from_request(request)
        metrics = services.observability.sre.render_prometheus_metrics()
        response = PlainTextResponse(content=metrics, media_type="text/plain; version=0.0.4")
        response.headers["X-Request-ID"] = request_id_from_request(request)
        response.headers["X-Trace-ID"] = str(getattr(request.state, "trace_id", request_id_from_request(request)))
        for key, value in services.api_lifecycle.response_headers(request.url.path).items():
            response.headers[key] = value
        return response

    @app.get("/service/api/lifecycle")
    def service_api_lifecycle(request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        return {
            "request_id": request_id_from_request(request),
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "policy": services.api_lifecycle.describe(),
            "compat_contract_path": str(services.config.api_compat_contract_path),
        }

    @app.post("/service/runs/kill-switch")
    def service_runs_kill_switch(payload: RunKillSwitchRequest, request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        request_id = request_id_from_request(request)
        sign_payload = {
            "reason": payload.reason,
            "include_running": bool(payload.include_running),
            "include_queued": bool(payload.include_queued),
            "limit": int(payload.limit),
        }
        try:
            summary = services.agent_manager.kill_switch_runs(
                actor=auth.user_id,
                reason=payload.reason,
                include_running=bool(payload.include_running),
                include_queued=bool(payload.include_queued),
                limit=int(payload.limit),
            )
            try:
                receipt = services.security_manager.signed_action(
                    action="agent_runs_kill_switch",
                    payload=sign_payload,
                    request_id=request_id,
                    actor=auth.user_id,
                    target_type="agent_run",
                    target_id="*",
                    details=summary,
                )
            except Exception:
                receipt = {}
            return {
                "request_id": request_id,
                "actor": auth.user_id,
                "scopes": sorted(auth.scopes),
                "kill_switch": summary,
                "action_receipt": receipt,
            }
        except ValueError as exc:
            try:
                services.security_manager.signed_action(
                    action="agent_runs_kill_switch",
                    payload=sign_payload,
                    request_id=request_id,
                    actor=auth.user_id,
                    target_type="agent_run",
                    target_id="*",
                    status="failed",
                    details={"error": str(exc)},
                )
            except Exception:
                pass
            raise ValidationError(str(exc)) from exc
        except AmaryllisError:
            raise
        except Exception as exc:
            try:
                services.security_manager.signed_action(
                    action="agent_runs_kill_switch",
                    payload=sign_payload,
                    request_id=request_id,
                    actor=auth.user_id,
                    target_type="agent_run",
                    target_id="*",
                    status="failed",
                    details={"error": str(exc)},
                )
            except Exception:
                pass
            raise ProviderError(str(exc)) from exc

    return app


app = create_app()
