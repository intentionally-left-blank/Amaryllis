from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
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
from api.flow_api import router as flow_router
from api.inbox_api import router as inbox_router
from api.memory_api import router as memory_router
from api.model_api import router as model_router
from api.news_api import router as news_router
from api.privacy_api import router as privacy_router
from api.provider_auth_api import router as provider_auth_router
from api.security_api import router as security_router
from api.supervisor_api import router as supervisor_router
from api.tool_api import router as tool_router
from api.voice_api import router as voice_router
from automation.automation_scheduler import AutomationScheduler
from controller.meta_controller import MetaController
from kernel.contracts import CognitionBackendContract
from kernel.orchestration import KernelExecutorAdapter
from memory.episodic_memory import EpisodicMemory
from memory.consolidation_worker import MemoryConsolidationWorker
from memory.memory_manager import MemoryManager
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory
from memory.working_memory import WorkingMemory
from news.pipeline import NewsIngestionPipeline
from models.cognition_backends import (
    DeterministicCognitionBackend,
    ModelManagerCognitionBackend,
    ensure_cognition_backend_contract,
)
from models.model_manager import ModelManager
from planner.planner import Planner
from runtime.backup import BackupManager, BackupScheduler
from runtime.compliance import ComplianceManager
from runtime.api_lifecycle import APILifecyclePolicy, canonical_api_path
from runtime.auth import AuthContext, AuthManager, auth_context_from_request
from runtime.autonomy_circuit_breaker import (
    AutonomyCircuitBreaker,
    normalize_circuit_breaker_scope,
    SUPPORTED_CIRCUIT_BREAKER_SCOPE_TYPES,
)
from runtime.config import AppConfig
from runtime.entitlements import EntitlementResolver
from runtime.errors import AmaryllisError, InternalError, PermissionDeniedError, ProviderError, ValidationError
from runtime.observability import ObservabilityManager, ObservabilityTelemetry, SLOTargets
from runtime.provider_sessions import ProviderSessionManager
from runtime.qos_governor import QoSGovernor, QoSThresholds, SUPPORTED_THERMAL_STATES
from runtime.security import LocalIdentityManager, SecurityManager
from runtime.telemetry import LocalTelemetry
from sources.registry import SourceConnectorRegistry
from storage.database import Database
from storage.vector_store import VectorStore
from supervisor.task_graph_manager import SupervisorTaskGraphManager
from tasks.task_executor import TaskExecutor
from flow.session_manager import UnifiedSessionManager
from tools.mcp_client_registry import MCPClientRegistry
from tools.autonomy_policy import AutonomyPolicy
from tools.browser_action_adapter import BrowserActionAdapter, StubBrowserActionAdapter, register_browser_action_tool
from tools.desktop_action_adapter import (
    DesktopActionAdapter,
    create_default_desktop_action_adapter,
    register_desktop_action_tool,
)
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
    model_manager: CognitionBackendContract
    memory_manager: MemoryManager
    tool_registry: ToolRegistry
    browser_adapter: BrowserActionAdapter
    desktop_adapter: DesktopActionAdapter
    voice_session_manager: VoiceSessionManager
    flow_session_manager: UnifiedSessionManager
    stt_adapter: STTAdapter
    tool_executor: ToolExecutor
    meta_controller: MetaController
    planner: Planner
    task_executor: TaskExecutor
    agent_run_manager: AgentRunManager
    agent_manager: AgentManager
    supervisor_manager: SupervisorTaskGraphManager
    automation_scheduler: AutomationScheduler
    memory_consolidation_worker: MemoryConsolidationWorker | None
    backup_manager: BackupManager
    backup_scheduler: BackupScheduler | None
    mcp_registry: MCPClientRegistry | None
    telemetry: Any
    local_telemetry: LocalTelemetry
    observability: ObservabilityManager
    qos_governor: QoSGovernor
    api_lifecycle: APILifecyclePolicy
    identity_manager: LocalIdentityManager
    security_manager: SecurityManager
    compliance_manager: ComplianceManager
    auth_manager: AuthManager
    autonomy_circuit_breaker: AutonomyCircuitBreaker
    provider_session_manager: ProviderSessionManager
    entitlement_resolver: EntitlementResolver
    source_connectors: SourceConnectorRegistry
    news_pipeline: NewsIngestionPipeline


class RunKillSwitchRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)
    include_running: bool = True
    include_queued: bool = True
    limit: int = Field(default=5000, ge=1, le=50000)


class RunAutonomyCircuitBreakerUpdateRequest(BaseModel):
    action: str = Field(default="arm")
    reason: str | None = Field(default=None, max_length=4000)
    scope_type: str = Field(default="global")
    scope_user_id: str | None = Field(default=None, max_length=512)
    scope_agent_id: str | None = Field(default=None, max_length=512)
    apply_kill_switch: bool = True
    include_running: bool = True
    include_queued: bool = True
    limit: int = Field(default=5000, ge=1, le=50000)


class QoSModeUpdateRequest(BaseModel):
    mode: str | None = Field(default=None)
    auto_enabled: bool | None = None
    thermal_state: str | None = Field(default=None)


class QoSThermalUpdateRequest(BaseModel):
    thermal_state: str = Field(default="unknown")


AUTONOMY_CIRCUIT_BREAKER_AUDIT_EVENT_TYPE = "autonomy_circuit_breaker_transition"


def _validate_thermal_state(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPORTED_THERMAL_STATES:
        raise ValidationError("thermal_state must be one of: " + ", ".join(SUPPORTED_THERMAL_STATES))
    return normalized


def _normalize_circuit_breaker_action(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"arm", "disarm"}:
        raise ValidationError("action must be one of: arm, disarm")
    return normalized


def _normalize_circuit_breaker_timeline_transition(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in {"arm", "disarm"}:
        raise ValidationError("transition must be one of: arm, disarm")
    return normalized


def _normalize_circuit_breaker_timeline_scope_type(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized not in SUPPORTED_CIRCUIT_BREAKER_SCOPE_TYPES:
        raise ValidationError(
            "scope_type must be one of: " + ", ".join(SUPPORTED_CIRCUIT_BREAKER_SCOPE_TYPES)
        )
    return normalized


def _extract_circuit_breaker_timeline_transition(item: dict[str, Any]) -> dict[str, Any]:
    details = item.get("details") if isinstance(item, dict) else {}
    details = details if isinstance(details, dict) else {}
    transition = details.get("transition")
    if isinstance(transition, dict):
        limit_value = transition.get("limit")
        try:
            normalized_limit = int(limit_value) if str(limit_value or "").strip() else None
        except Exception:
            normalized_limit = None
        return {
            "action": str(transition.get("action") or "").strip().lower() or None,
            "reason": str(transition.get("reason") or "").strip() or None,
            "scope_type": str(transition.get("scope_type") or "").strip().lower() or None,
            "scope_user_id": str(transition.get("scope_user_id") or "").strip() or None,
            "scope_agent_id": str(transition.get("scope_agent_id") or "").strip() or None,
            "apply_kill_switch": bool(transition.get("apply_kill_switch")),
            "include_running": bool(transition.get("include_running")),
            "include_queued": bool(transition.get("include_queued")),
            "limit": normalized_limit,
        }

    circuit_breaker = details.get("circuit_breaker")
    circuit_breaker = circuit_breaker if isinstance(circuit_breaker, dict) else {}
    target_scope = circuit_breaker.get("target_scope")
    target_scope = target_scope if isinstance(target_scope, dict) else {}
    scope = target_scope.get("scope")
    scope = scope if isinstance(scope, dict) else {}
    action = str(details.get("action") or target_scope.get("action") or "").strip().lower() or None
    return {
        "action": action,
        "reason": str(circuit_breaker.get("reason") or "").strip() or None,
        "scope_type": str(scope.get("scope_type") or "").strip().lower() or None,
        "scope_user_id": str(scope.get("scope_user_id") or "").strip() or None,
        "scope_agent_id": str(scope.get("scope_agent_id") or "").strip() or None,
        "apply_kill_switch": False,
        "include_running": False,
        "include_queued": False,
        "limit": None,
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _breaker_error_is_blocked(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return "autonomy circuit breaker" in text and "block" in text


def _build_circuit_breaker_recovery_guidance(
    *,
    circuit_breaker: dict[str, Any],
    observability_snapshot: dict[str, Any],
    recent_timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    armed = bool(circuit_breaker.get("armed"))
    persistence = circuit_breaker.get("persistence") if isinstance(circuit_breaker.get("persistence"), dict) else {}
    restore_status = str((persistence or {}).get("restore_status") or "").strip().lower() or "unknown"
    restore_error = str((persistence or {}).get("restore_error") or "").strip() or None

    slo = observability_snapshot.get("slo") if isinstance(observability_snapshot.get("slo"), dict) else {}
    sli = observability_snapshot.get("sli") if isinstance(observability_snapshot.get("sli"), dict) else {}
    incidents = (
        observability_snapshot.get("incidents") if isinstance(observability_snapshot.get("incidents"), dict) else {}
    )
    request_sli = sli.get("requests") if isinstance(sli.get("requests"), dict) else {}
    run_sli = sli.get("runs") if isinstance(sli.get("runs"), dict) else {}

    availability = _safe_float(request_sli.get("availability"), default=1.0)
    availability_target = _safe_float(slo.get("request_availability_target"), default=1.0)
    latency_p95_ms = _safe_float(request_sli.get("latency_p95_ms"), default=0.0)
    latency_target_ms = _safe_float(slo.get("request_latency_p95_ms_target"), default=0.0)
    run_success_rate = _safe_float(run_sli.get("success_rate"), default=1.0)
    run_success_target = _safe_float(slo.get("run_success_target"), default=1.0)
    open_incidents = _safe_int(incidents.get("open_count"), default=0)

    latest_transition = recent_timeline[-1] if recent_timeline else {}
    latest_transition_payload = (
        latest_transition.get("transition") if isinstance(latest_transition.get("transition"), dict) else {}
    )
    latest_action = str((latest_transition_payload or {}).get("action") or "").strip().lower() or None
    latest_scope_type = str((latest_transition_payload or {}).get("scope_type") or "").strip().lower() or None
    latest_reason = str((latest_transition_payload or {}).get("reason") or "").strip() or None

    recommendations: list[str] = []
    status = "ready"
    priority = "low"
    summary = "Breaker is disarmed and no immediate recovery actions are required."

    if restore_status == "fail_safe_armed":
        status = "action_required"
        priority = "critical"
        summary = "Breaker entered fail-safe mode after state recovery failure; operator action is required."
        recommendations.append(
            "Validate breaker state file integrity and confirm incident context before disarming fail-safe global scope."
        )
        recommendations.append(
            "Use timeline filters by request_id to verify latest transition ownership and reason chain."
        )
        if restore_error:
            recommendations.append("Investigate persistence restore error and fix root cause before resuming execute mode.")
    elif armed:
        status = "action_required"
        priority = "high"
        summary = "Breaker is armed; complete incident containment and controlled recovery workflow."
        recommendations.append(
            "Confirm containment scope and incident reason in timeline before changing breaker state."
        )
        if latest_scope_type == "global" and latest_action == "arm":
            recommendations.append(
                "For global arms, verify queued/running runs were intentionally handled (kill-switch or manual cancellation)."
            )
        recommendations.append(
            "When mitigation is complete, disarm explicitly and verify execute-mode run creation with a smoke request."
        )
    elif open_incidents > 0:
        status = "monitoring"
        priority = "medium"
        summary = "Breaker is disarmed but observability still reports open incidents."
        recommendations.append(
            "Keep breaker disarmed only if incident impact is contained and open incidents are acknowledged."
        )
        recommendations.append(
            "Track incident cooldown and verify SLO metrics return within target thresholds."
        )

    if availability < availability_target:
        status = "monitoring" if status == "ready" else status
        priority = "medium" if priority == "low" else priority
        recommendations.append("Request availability is below target; avoid aggressive unfreeze until stability recovers.")
    if latency_target_ms > 0 and latency_p95_ms > latency_target_ms:
        status = "monitoring" if status == "ready" else status
        recommendations.append("Request latency p95 exceeds target; prefer staged recovery with canary validation.")
    if run_success_rate < run_success_target:
        status = "monitoring" if status == "ready" else status
        recommendations.append("Run success rate is below target; verify failure-class trends before scaling execute load.")

    if not recommendations:
        recommendations.append("No immediate actions. Continue periodic breaker and SLO health checks.")

    return {
        "status": status,
        "priority": priority,
        "summary": summary,
        "latest_transition": {
            "action": latest_action,
            "scope_type": latest_scope_type,
            "reason": latest_reason,
            "request_id": latest_transition.get("request_id"),
            "created_at": latest_transition.get("created_at"),
        },
        "slo_context": {
            "request_availability": round(availability, 6),
            "request_availability_target": round(availability_target, 6),
            "request_latency_p95_ms": round(latency_p95_ms, 3),
            "request_latency_p95_ms_target": round(latency_target_ms, 3),
            "run_success_rate": round(run_success_rate, 6),
            "run_success_target": round(run_success_target, 6),
            "open_incidents": open_incidents,
        },
        "recommendations": recommendations,
    }


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
    qos_governor = QoSGovernor(
        initial_mode=config.qos_mode,
        initial_thermal_state=config.qos_thermal_state,
        auto_enabled=config.qos_auto_enabled,
        thresholds=QoSThresholds(
            ttft_target_ms=config.qos_ttft_target_ms,
            ttft_critical_ms=config.qos_ttft_critical_ms,
            request_latency_target_ms=config.qos_request_latency_target_ms,
            request_latency_critical_ms=config.qos_request_latency_critical_ms,
            kv_pressure_target_events=config.qos_kv_pressure_target_events,
            kv_pressure_critical_events=config.qos_kv_pressure_critical_events,
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
    provider_session_manager = ProviderSessionManager(database=database)
    entitlement_resolver = EntitlementResolver(config=config, database=database)
    source_connectors = SourceConnectorRegistry()
    news_pipeline = NewsIngestionPipeline(source_registry=source_connectors)

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
    desktop_adapter: DesktopActionAdapter = create_default_desktop_action_adapter()
    register_desktop_action_tool(tool_registry, desktop_adapter)
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

    autonomy_circuit_breaker = AutonomyCircuitBreaker(
        state_path=config.autonomy_circuit_breaker_state_path,
    )

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
        autonomy_policy=AutonomyPolicy(
            level=config.autonomy_level,
            policy_pack_path=config.autonomy_policy_pack_path,
        ),
        autonomy_circuit_breaker=autonomy_circuit_breaker,
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
    flow_session_manager = UnifiedSessionManager(telemetry_emitter=telemetry.emit)
    stt_adapter = create_stt_adapter_from_env()

    raw_model_manager = ModelManager(
        config=config,
        database=database,
        entitlement_resolver=entitlement_resolver,
    )
    backend_mode = str(os.getenv("AMARYLLIS_COGNITION_BACKEND", "model_manager")).strip().lower()
    if backend_mode in {"deterministic", "synthetic"}:
        model_manager = ensure_cognition_backend_contract(DeterministicCognitionBackend())
        logger.info("cognition_backend_selected backend=deterministic")
    else:
        model_manager = ensure_cognition_backend_contract(ModelManagerCognitionBackend(raw_model_manager))
        logger.info("cognition_backend_selected backend=model_manager")

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
        autonomy_circuit_breaker=autonomy_circuit_breaker,
    )
    agent_run_manager.start()
    agent_manager = AgentManager(
        database=database,
        task_executor=kernel_executor,
        run_manager=agent_run_manager,
    )
    supervisor_manager = SupervisorTaskGraphManager(
        agent_manager=agent_manager,
        database=database,
        telemetry_emitter=telemetry.emit,
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
        desktop_adapter=desktop_adapter,
        voice_session_manager=voice_session_manager,
        flow_session_manager=flow_session_manager,
        stt_adapter=stt_adapter,
        tool_executor=tool_executor,
        meta_controller=meta_controller,
        planner=planner,
        task_executor=task_executor,
        agent_run_manager=agent_run_manager,
        agent_manager=agent_manager,
        supervisor_manager=supervisor_manager,
        automation_scheduler=automation_scheduler,
        memory_consolidation_worker=memory_consolidation_worker,
        backup_manager=backup_manager,
        backup_scheduler=backup_scheduler,
        mcp_registry=mcp_registry,
        telemetry=telemetry,
        local_telemetry=local_telemetry,
        observability=observability,
        qos_governor=qos_governor,
        api_lifecycle=api_lifecycle,
        identity_manager=identity_manager,
        security_manager=security_manager,
        compliance_manager=compliance_manager,
        auth_manager=auth_manager,
        autonomy_circuit_breaker=autonomy_circuit_breaker,
        provider_session_manager=provider_session_manager,
        entitlement_resolver=entitlement_resolver,
        source_connectors=source_connectors,
        news_pipeline=news_pipeline,
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

    def _recent_circuit_breaker_timeline_items(*, limit: int = 50) -> list[dict[str, Any]]:
        raw_items = services.security_manager.list_audit_events(
            limit=max(1, min(int(limit), 500)),
            event_type=AUTONOMY_CIRCUIT_BREAKER_AUDIT_EVENT_TYPE,
            action="agent_runs_autonomy_circuit_breaker",
        )
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            transition = _extract_circuit_breaker_timeline_transition(raw)
            items.append(
                {
                    "id": raw.get("id"),
                    "created_at": raw.get("created_at"),
                    "status": raw.get("status"),
                    "actor": raw.get("actor"),
                    "request_id": raw.get("request_id"),
                    "transition": transition,
                }
            )
        return items

    def _autonomy_circuit_breaker_domain_impact_snapshot(
        *,
        event_limit: int = 500,
        supervisor_graph_limit: int = 200,
        supervisor_timeline_limit: int = 400,
    ) -> dict[str, Any]:
        bounded_event_limit = max(1, min(int(event_limit), 5000))
        bounded_graph_limit = max(1, min(int(supervisor_graph_limit), 2000))
        bounded_timeline_limit = max(1, min(int(supervisor_timeline_limit), 2000))

        run_events: list[dict[str, Any]] = []
        run_events.extend(
            services.security_manager.list_audit_events(
                limit=bounded_event_limit,
                event_type="signed_action",
                action="agent_run_create",
                status="failed",
            )
        )
        run_events.extend(
            services.security_manager.list_audit_events(
                limit=bounded_event_limit,
                event_type="signed_action",
                action="agent_run_dispatch",
                status="failed",
            )
        )
        run_blocked_items: list[dict[str, Any]] = []
        run_blocked_by_action = {"agent_run_create": 0, "agent_run_dispatch": 0}
        run_blocked_request_ids: set[str] = set()
        run_last_blocked_at: str | None = None
        for item in run_events:
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            error_message = str(details.get("error") or "")
            if not _breaker_error_is_blocked(error_message):
                continue
            run_blocked_items.append(item)
            action = str(item.get("action") or "").strip()
            if action in run_blocked_by_action:
                run_blocked_by_action[action] = int(run_blocked_by_action[action]) + 1
            request_id = str(item.get("request_id") or "").strip()
            if request_id:
                run_blocked_request_ids.add(request_id)
            created_at = str(item.get("created_at") or "").strip() or None
            if created_at and (run_last_blocked_at is None or created_at > run_last_blocked_at):
                run_last_blocked_at = created_at

        automation_events = services.database.list_recent_automation_events(limit=bounded_event_limit)
        automation_blocked_items = [
            item
            for item in automation_events
            if str(item.get("event_type") or "").strip().lower() == "run_blocked_autonomy_circuit_breaker"
        ]
        automation_ids_with_blocks = {
            str(item.get("automation_id") or "").strip()
            for item in automation_blocked_items
            if str(item.get("automation_id") or "").strip()
        }
        automation_last_blocked_at: str | None = None
        for item in automation_blocked_items:
            created_at = str(item.get("created_at") or "").strip() or None
            if created_at and (automation_last_blocked_at is None or created_at > automation_last_blocked_at):
                automation_last_blocked_at = created_at

        supervisor_graphs = services.supervisor_manager.list_graphs(limit=bounded_graph_limit)
        supervisor_blocked_total = 0
        supervisor_graphs_with_blocks: set[str] = set()
        supervisor_last_blocked_at: str | None = None
        for graph in supervisor_graphs:
            graph_id = str(graph.get("id") or "").strip()
            timeline = graph.get("timeline")
            if not isinstance(timeline, list) or not timeline:
                continue
            start_index = max(0, len(timeline) - bounded_timeline_limit)
            for item in timeline[start_index:]:
                if not isinstance(item, dict):
                    continue
                event_name = str(item.get("event") or "").strip()
                if event_name != "node_run_blocked_autonomy_circuit_breaker":
                    continue
                supervisor_blocked_total += 1
                if graph_id:
                    supervisor_graphs_with_blocks.add(graph_id)
                created_at = str(item.get("at") or "").strip() or None
                if created_at and (supervisor_last_blocked_at is None or created_at > supervisor_last_blocked_at):
                    supervisor_last_blocked_at = created_at

        runs_blocked = len(run_blocked_items)
        automations_blocked = len(automation_blocked_items)
        supervisor_blocked = int(supervisor_blocked_total)
        domains_with_blocks: list[str] = []
        if runs_blocked > 0:
            domains_with_blocks.append("runs")
        if automations_blocked > 0:
            domains_with_blocks.append("automations")
        if supervisor_blocked > 0:
            domains_with_blocks.append("supervisor")

        return {
            "window": {
                "event_limit": bounded_event_limit,
                "supervisor_graph_limit": bounded_graph_limit,
                "supervisor_timeline_limit": bounded_timeline_limit,
            },
            "runs": {
                "blocked_events": runs_blocked,
                "blocked_by_action": run_blocked_by_action,
                "blocked_request_ids": len(run_blocked_request_ids),
                "last_blocked_at": run_last_blocked_at,
                "source": "security_audit_events",
            },
            "automations": {
                "blocked_events": automations_blocked,
                "automations_with_blocks": len(automation_ids_with_blocks),
                "last_blocked_at": automation_last_blocked_at,
                "source": "automation_events",
            },
            "supervisor": {
                "blocked_events": supervisor_blocked,
                "graphs_with_blocks": len(supervisor_graphs_with_blocks),
                "last_blocked_at": supervisor_last_blocked_at,
                "source": "supervisor_graph_timeline",
            },
            "summary": {
                "blocked_total": runs_blocked + automations_blocked + supervisor_blocked,
                "domains_with_blocks": domains_with_blocks,
                "domains_evaluated": ["runs", "automations", "supervisor"],
            },
        }

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
    app.include_router(flow_router)
    app.include_router(inbox_router)
    app.include_router(memory_router)
    app.include_router(news_router)
    app.include_router(privacy_router)
    app.include_router(provider_auth_router)
    app.include_router(supervisor_router)
    app.include_router(tool_router)
    app.include_router(voice_router)
    app.include_router(backup_router)
    # Versioned API aliases for lifecycle-managed stable contract.
    app.include_router(model_router, prefix="/v1")
    app.include_router(agent_router, prefix="/v1")
    app.include_router(automation_router, prefix="/v1")
    app.include_router(flow_router, prefix="/v1")
    app.include_router(inbox_router, prefix="/v1")
    app.include_router(memory_router, prefix="/v1")
    app.include_router(news_router, prefix="/v1")
    app.include_router(privacy_router, prefix="/v1")
    app.include_router(provider_auth_router, prefix="/v1")
    app.include_router(supervisor_router, prefix="/v1")
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
            "autonomy_circuit_breaker": services.autonomy_circuit_breaker.snapshot(),
            "providers": checks,
        }

    @app.get("/service/observability/slo")
    def service_observability_slo(request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        snapshot = services.observability.sre.snapshot()
        qos = services.qos_governor.reconcile(snapshot=snapshot)
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
            "qos": qos,
            "snapshot": snapshot,
        }

    @app.get("/service/qos")
    def service_qos_status(request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        request_id = request_id_from_request(request)
        snapshot = services.observability.sre.snapshot()
        qos = services.qos_governor.reconcile(snapshot=snapshot)
        return {
            "request_id": request_id,
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "qos": qos,
        }

    @app.post("/service/qos/mode")
    def service_qos_set_mode(payload: QoSModeUpdateRequest, request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        request_id = request_id_from_request(request)
        if payload.mode is None and payload.auto_enabled is None and payload.thermal_state is None:
            raise ValidationError("mode, auto_enabled, or thermal_state must be provided")
        thermal_state: str | None = None
        if payload.thermal_state is not None:
            thermal_state = _validate_thermal_state(payload.thermal_state)
        snapshot = services.observability.sre.snapshot()
        try:
            qos = services.qos_governor.set_mode(
                mode=payload.mode,
                auto_enabled=payload.auto_enabled,
                thermal_state=thermal_state,
                snapshot=snapshot,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return {
            "request_id": request_id,
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "qos": qos,
        }

    @app.post("/service/qos/thermal")
    def service_qos_set_thermal(payload: QoSThermalUpdateRequest, request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        request_id = request_id_from_request(request)
        thermal_state = _validate_thermal_state(payload.thermal_state)
        snapshot = services.observability.sre.snapshot()
        qos = services.qos_governor.set_thermal_state(
            thermal_state=thermal_state,
            snapshot=snapshot,
        )
        return {
            "request_id": request_id,
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "qos": qos,
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

    @app.get("/service/runs/autonomy-circuit-breaker")
    def service_runs_autonomy_circuit_breaker_status(request: Request) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        circuit_breaker = services.autonomy_circuit_breaker.snapshot()
        recovery_guidance = _build_circuit_breaker_recovery_guidance(
            circuit_breaker=circuit_breaker,
            observability_snapshot=services.observability.sre.snapshot(),
            recent_timeline=_recent_circuit_breaker_timeline_items(limit=50),
        )
        return {
            "request_id": request_id_from_request(request),
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "circuit_breaker": circuit_breaker,
            "recovery_guidance": recovery_guidance,
        }

    @app.get("/service/runs/autonomy-circuit-breaker/domains")
    def service_runs_autonomy_circuit_breaker_domains(
        request: Request,
        limit: int = Query(default=500, ge=1, le=5000),
        supervisor_graph_limit: int = Query(default=200, ge=1, le=2000),
        supervisor_timeline_limit: int = Query(default=400, ge=1, le=2000),
    ) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        circuit_breaker = services.autonomy_circuit_breaker.snapshot()
        domain_impact = _autonomy_circuit_breaker_domain_impact_snapshot(
            event_limit=limit,
            supervisor_graph_limit=supervisor_graph_limit,
            supervisor_timeline_limit=supervisor_timeline_limit,
        )
        recovery_guidance = _build_circuit_breaker_recovery_guidance(
            circuit_breaker=circuit_breaker,
            observability_snapshot=services.observability.sre.snapshot(),
            recent_timeline=_recent_circuit_breaker_timeline_items(limit=50),
        )
        return {
            "request_id": request_id_from_request(request),
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "circuit_breaker": circuit_breaker,
            "domain_impact": domain_impact,
            "recovery_guidance": recovery_guidance,
        }

    @app.get("/service/runs/autonomy-circuit-breaker/timeline")
    def service_runs_autonomy_circuit_breaker_timeline(
        request: Request,
        limit: int = Query(default=200, ge=1, le=2000),
        status: str | None = Query(default=None),
        actor: str | None = Query(default=None),
        transition: str | None = Query(default=None),
        scope_type: str | None = Query(default=None),
        scope_request_id: str | None = Query(default=None, alias="request_id"),
    ) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        normalized_transition = _normalize_circuit_breaker_timeline_transition(transition)
        normalized_scope_type = _normalize_circuit_breaker_timeline_scope_type(scope_type)
        normalized_request_id = str(scope_request_id or "").strip() or None
        raw_items = services.security_manager.list_audit_events(
            limit=max(1, min(int(limit), 2000)),
            event_type=AUTONOMY_CIRCUIT_BREAKER_AUDIT_EVENT_TYPE,
            action="agent_runs_autonomy_circuit_breaker",
            status=status,
            actor=actor,
            request_id=normalized_request_id,
        )
        items: list[dict[str, Any]] = []
        for raw in raw_items:
            transition_payload = _extract_circuit_breaker_timeline_transition(raw)
            transition_action = str(transition_payload.get("action") or "").strip().lower() or None
            transition_scope_type = str(transition_payload.get("scope_type") or "").strip().lower() or None
            if normalized_transition is not None and transition_action != normalized_transition:
                continue
            if normalized_scope_type is not None and transition_scope_type != normalized_scope_type:
                continue

            details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
            details = details if isinstance(details, dict) else {}
            circuit_breaker_raw = (
                details.get("circuit_breaker") if isinstance(details.get("circuit_breaker"), dict) else {}
            )
            kill_switch_raw = details.get("kill_switch") if isinstance(details.get("kill_switch"), dict) else None
            item = {
                "id": raw.get("id"),
                "created_at": raw.get("created_at"),
                "status": raw.get("status"),
                "event_type": raw.get("event_type"),
                "action": raw.get("action"),
                "actor": raw.get("actor"),
                "request_id": raw.get("request_id"),
                "transition": transition_payload,
                "circuit_breaker": {
                    "revision": circuit_breaker_raw.get("revision"),
                    "status": circuit_breaker_raw.get("status"),
                    "reason": circuit_breaker_raw.get("reason"),
                    "target_scope": circuit_breaker_raw.get("target_scope"),
                },
                "signature": raw.get("signature") if isinstance(raw.get("signature"), dict) else {},
            }
            if kill_switch_raw is not None:
                item["kill_switch"] = kill_switch_raw
            error_message = str(details.get("error") or "").strip()
            if error_message:
                item["error"] = error_message
            items.append(item)

        recovery_guidance = _build_circuit_breaker_recovery_guidance(
            circuit_breaker=services.autonomy_circuit_breaker.snapshot(),
            observability_snapshot=services.observability.sre.snapshot(),
            recent_timeline=_recent_circuit_breaker_timeline_items(limit=50),
        )
        return {
            "request_id": request_id_from_request(request),
            "actor": auth.user_id,
            "scopes": sorted(auth.scopes),
            "count": len(items),
            "items": items,
            "recovery_guidance": recovery_guidance,
        }

    @app.post("/service/runs/autonomy-circuit-breaker")
    def service_runs_autonomy_circuit_breaker_update(
        payload: RunAutonomyCircuitBreakerUpdateRequest,
        request: Request,
    ) -> dict[str, Any]:
        auth = auth_context_from_request(request)
        request_id = request_id_from_request(request)
        action = _normalize_circuit_breaker_action(payload.action)
        try:
            scope_type, scope_user_id, scope_agent_id = normalize_circuit_breaker_scope(
                scope_type=payload.scope_type,
                scope_user_id=payload.scope_user_id,
                scope_agent_id=payload.scope_agent_id,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if (
            action == "arm"
            and payload.apply_kill_switch
            and not payload.include_running
            and not payload.include_queued
        ):
            raise ValidationError(
                "When apply_kill_switch=true, include_running and/or include_queued must be true."
            )
        sign_payload = {
            "action": action,
            "reason": payload.reason,
            "scope_type": scope_type,
            "scope_user_id": scope_user_id,
            "scope_agent_id": scope_agent_id,
            "apply_kill_switch": bool(payload.apply_kill_switch),
            "include_running": bool(payload.include_running),
            "include_queued": bool(payload.include_queued),
            "limit": int(payload.limit),
        }
        transition_details = {
            "action": action,
            "reason": str(payload.reason or "").strip() or None,
            "scope_type": scope_type,
            "scope_user_id": scope_user_id,
            "scope_agent_id": scope_agent_id,
            "apply_kill_switch": bool(payload.apply_kill_switch),
            "include_running": bool(payload.include_running),
            "include_queued": bool(payload.include_queued),
            "limit": int(payload.limit),
        }
        try:
            kill_switch_summary: dict[str, Any] | None = None
            if action == "arm":
                state = services.autonomy_circuit_breaker.arm(
                    actor=auth.user_id,
                    reason=payload.reason,
                    request_id=request_id,
                    scope_type=scope_type,
                    scope_user_id=scope_user_id,
                    scope_agent_id=scope_agent_id,
                )
                if payload.apply_kill_switch:
                    kill_switch_kwargs: dict[str, Any] = {
                        "actor": auth.user_id,
                        "reason": payload.reason,
                        "include_running": bool(payload.include_running),
                        "include_queued": bool(payload.include_queued),
                        "limit": int(payload.limit),
                    }
                    if scope_type == "user":
                        kill_switch_kwargs["user_id"] = scope_user_id
                    elif scope_type == "agent":
                        kill_switch_kwargs["agent_id"] = scope_agent_id
                    kill_switch_summary = services.agent_manager.kill_switch_runs(
                        **kill_switch_kwargs,
                    )
            else:
                state = services.autonomy_circuit_breaker.disarm(
                    actor=auth.user_id,
                    reason=payload.reason,
                    request_id=request_id,
                    scope_type=scope_type,
                    scope_user_id=scope_user_id,
                    scope_agent_id=scope_agent_id,
                )

            receipt_details: dict[str, Any] = {
                "action": action,
                "transition": transition_details,
                "circuit_breaker": state,
            }
            if kill_switch_summary is not None:
                receipt_details["kill_switch"] = kill_switch_summary
            try:
                receipt = services.security_manager.signed_action(
                    action="agent_runs_autonomy_circuit_breaker",
                    payload=sign_payload,
                    request_id=request_id,
                    actor=auth.user_id,
                    target_type="agent_run",
                    target_id="*",
                    event_type=AUTONOMY_CIRCUIT_BREAKER_AUDIT_EVENT_TYPE,
                    details=receipt_details,
                )
            except Exception:
                receipt = {}
            payload_out = {
                "request_id": request_id,
                "actor": auth.user_id,
                "scopes": sorted(auth.scopes),
                "circuit_breaker": state,
                "action_receipt": receipt,
                "recovery_guidance": _build_circuit_breaker_recovery_guidance(
                    circuit_breaker=services.autonomy_circuit_breaker.snapshot(),
                    observability_snapshot=services.observability.sre.snapshot(),
                    recent_timeline=_recent_circuit_breaker_timeline_items(limit=50),
                ),
            }
            if kill_switch_summary is not None:
                payload_out["kill_switch"] = kill_switch_summary
            return payload_out
        except ValueError as exc:
            try:
                services.security_manager.signed_action(
                    action="agent_runs_autonomy_circuit_breaker",
                    payload=sign_payload,
                    request_id=request_id,
                    actor=auth.user_id,
                    target_type="agent_run",
                    target_id="*",
                    event_type=AUTONOMY_CIRCUIT_BREAKER_AUDIT_EVENT_TYPE,
                    status="failed",
                    details={"action": action, "transition": transition_details, "error": str(exc)},
                )
            except Exception:
                pass
            raise ValidationError(str(exc)) from exc
        except AmaryllisError:
            raise
        except Exception as exc:
            try:
                services.security_manager.signed_action(
                    action="agent_runs_autonomy_circuit_breaker",
                    payload=sign_payload,
                    request_id=request_id,
                    actor=auth.user_id,
                    target_type="agent_run",
                    target_id="*",
                    event_type=AUTONOMY_CIRCUIT_BREAKER_AUDIT_EVENT_TYPE,
                    status="failed",
                    details={"action": action, "transition": transition_details, "error": str(exc)},
                )
            except Exception:
                pass
            raise ProviderError(str(exc)) from exc

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
                "circuit_breaker": services.autonomy_circuit_breaker.snapshot(),
                "recovery_guidance": _build_circuit_breaker_recovery_guidance(
                    circuit_breaker=services.autonomy_circuit_breaker.snapshot(),
                    observability_snapshot=services.observability.sre.snapshot(),
                    recent_timeline=_recent_circuit_breaker_timeline_items(limit=50),
                ),
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
