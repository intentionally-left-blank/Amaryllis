from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


class AppConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AuthTokenConfig:
    token: str
    user_id: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    app_version: str
    host: str
    port: int
    support_dir: Path
    models_dir: Path
    data_dir: Path
    backup_dir: Path
    evidence_dir: Path
    plugins_dir: Path
    database_path: Path
    vector_index_path: Path
    telemetry_path: Path
    observability_otel_enabled: bool
    observability_otlp_endpoint: str | None
    observability_slo_window_sec: float
    observability_request_availability_target: float
    observability_request_latency_p95_ms_target: float
    observability_run_success_target: float
    observability_min_request_samples: int
    observability_min_run_samples: int
    observability_incident_cooldown_sec: float
    backup_enabled: bool
    backup_interval_sec: float
    backup_retention_count: int
    backup_retention_days: int
    backup_verify_on_create: bool
    backup_restore_drill_enabled: bool
    backup_restore_drill_interval_sec: float
    api_version: str
    api_release_channel: str
    api_deprecation_sunset_days: int
    api_compat_contract_path: Path
    default_provider: str
    default_model: str
    ollama_base_url: str
    enable_ollama_fallback: bool
    openai_base_url: str
    openai_api_key: str | None
    openai_api_key_rotated_at: str | None
    openai_api_key_expires_at: str | None
    anthropic_base_url: str
    anthropic_api_key: str | None
    anthropic_api_key_rotated_at: str | None
    anthropic_api_key_expires_at: str | None
    openrouter_base_url: str
    openrouter_api_key: str | None
    openrouter_api_key_rotated_at: str | None
    openrouter_api_key_expires_at: str | None
    run_workers: int
    run_max_attempts: int
    run_attempt_timeout_sec: float
    run_lease_ttl_sec: float
    run_retry_backoff_sec: float
    run_retry_max_backoff_sec: float
    run_retry_jitter_sec: float
    run_budget_max_tokens: int
    run_budget_max_duration_sec: float
    run_budget_max_tool_calls: int
    run_budget_max_tool_errors: int
    automation_poll_sec: float
    automation_batch_size: int
    automation_escalation_warning: int
    automation_escalation_critical: int
    automation_escalation_disable: int
    automation_lease_ttl_sec: int
    automation_backoff_base_sec: float
    automation_backoff_max_sec: float
    automation_circuit_failure_threshold: int
    automation_circuit_open_sec: float
    task_max_duration_sec: float
    task_max_model_calls: int
    task_max_prompt_chars: int
    task_max_tool_rounds: int
    task_issue_parallel_workers: int
    task_issue_timeout_sec: float
    task_verifier_enabled: bool
    task_verifier_max_repair_attempts: int
    task_verifier_min_response_chars: int
    task_artifact_quality_enabled: bool
    task_artifact_quality_max_repair_attempts: int
    task_step_verifier_enabled: bool
    task_step_max_retries_default: int
    task_step_replan_max_attempts: int
    memory_consolidation_enabled: bool
    memory_consolidation_interval_sec: float
    memory_consolidation_semantic_limit: int
    memory_consolidation_max_users_per_tick: int
    memory_profile_decay_enabled: bool
    memory_profile_decay_half_life_days: float
    memory_profile_decay_floor: float
    memory_profile_decay_min_delta: float
    provider_retry_attempts: int
    provider_retry_backoff_sec: float
    provider_retry_jitter_sec: float
    provider_circuit_failure_threshold: int
    provider_circuit_cooldown_sec: float
    cloud_rate_window_sec: float
    cloud_rate_max_requests: int
    cloud_budget_window_sec: float
    cloud_budget_max_units: int
    security_profile: str
    security_allow_insecure_modes: bool
    auth_enabled: bool
    auth_tokens: tuple[AuthTokenConfig, ...]
    chat_max_messages: int
    chat_max_input_chars: int
    chat_max_tokens: int
    tool_approval_enforcement: str
    tool_isolation_profile: str
    tool_budget_window_sec: float
    tool_budget_max_calls_per_tool: int
    tool_budget_max_total_calls: int
    tool_budget_max_high_risk_calls: int
    blocked_tools: tuple[str, ...]
    allowed_high_risk_tools: tuple[str, ...]
    tool_python_exec_max_timeout_sec: int
    tool_python_exec_max_code_chars: int
    tool_filesystem_allow_write: bool
    tool_sandbox_enabled: bool
    tool_sandbox_timeout_sec: int
    tool_sandbox_max_cpu_sec: int
    tool_sandbox_max_memory_mb: int
    tool_sandbox_allow_network_tools: tuple[str, ...]
    tool_sandbox_allowed_roots: tuple[str, ...]
    plugin_signing_key: str | None
    plugin_signing_key_rotated_at: str | None
    plugin_signing_key_expires_at: str | None
    plugin_signing_mode: str
    plugin_runtime_mode: str
    mcp_endpoints: tuple[str, ...]
    mcp_timeout_sec: float
    mcp_failure_threshold: int
    mcp_quarantine_sec: float
    compliance_secret_rotation_max_age_days: int
    compliance_secret_expiry_warning_days: int
    compliance_identity_rotation_max_age_days: int
    compliance_access_review_max_age_days: int
    identity_path: Path

    @classmethod
    def from_env(cls) -> "AppConfig":
        support_dir = Path(
            os.getenv(
                "AMARYLLIS_SUPPORT_DIR",
                str(Path.home() / "Library" / "Application Support" / "amaryllis"),
            )
        ).expanduser()

        models_dir = Path(
            os.getenv(
                "AMARYLLIS_MODELS_DIR",
                str(support_dir / "models"),
            )
        ).expanduser()

        data_dir = Path(
            os.getenv(
                "AMARYLLIS_DATA_DIR",
                str(support_dir / "data"),
            )
        ).expanduser()

        plugins_dir = Path(
            os.getenv(
                "AMARYLLIS_PLUGINS_DIR",
                str(Path.cwd() / "plugins"),
            )
        ).expanduser()
        evidence_dir = Path(
            os.getenv(
                "AMARYLLIS_EVIDENCE_DIR",
                str(support_dir / "evidence"),
            )
        ).expanduser()
        backup_dir = Path(
            os.getenv(
                "AMARYLLIS_BACKUP_DIR",
                str(support_dir / "backups"),
            )
        ).expanduser()

        database_path = Path(
            os.getenv(
                "AMARYLLIS_DATABASE_PATH",
                str(data_dir / "amaryllis.db"),
            )
        ).expanduser()

        vector_index_path = Path(
            os.getenv(
                "AMARYLLIS_VECTOR_INDEX_PATH",
                str(data_dir / "semantic.index"),
            )
        ).expanduser()

        telemetry_path = Path(
            os.getenv(
                "AMARYLLIS_TELEMETRY_PATH",
                str(data_dir / "telemetry.jsonl"),
            )
        ).expanduser()
        api_compat_contract_path = Path(
            os.getenv(
                "AMARYLLIS_API_COMPAT_CONTRACT_PATH",
                str(Path.cwd() / "contracts" / "api_compat_v1.json"),
            )
        ).expanduser()
        identity_path = Path(
            os.getenv(
                "AMARYLLIS_IDENTITY_PATH",
                str(data_dir / "identity.json"),
            )
        ).expanduser()

        fallback_raw = os.getenv("AMARYLLIS_OLLAMA_FALLBACK", "true").strip().lower()
        enable_ollama_fallback = fallback_raw in {"1", "true", "yes", "on"}
        memory_consolidation_enabled = _parse_bool(
            os.getenv("AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED", "true")
        )
        blocked_tools = tuple(_csv_items(os.getenv("AMARYLLIS_BLOCKED_TOOLS", "")))
        allowed_high_risk_tools = tuple(_csv_items(os.getenv("AMARYLLIS_ALLOWED_HIGH_RISK_TOOLS", "")))
        mcp_endpoints = tuple(_csv_items(os.getenv("AMARYLLIS_MCP_ENDPOINTS", "")))
        tool_approval_enforcement = os.getenv(
            "AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT",
            "strict",
        ).strip().lower()
        if tool_approval_enforcement not in {"strict", "prompt_and_allow"}:
            tool_approval_enforcement = "strict"
        api_release_channel = os.getenv(
            "AMARYLLIS_RELEASE_CHANNEL",
            "stable",
        ).strip().lower()
        if api_release_channel not in {"alpha", "beta", "stable"}:
            api_release_channel = "stable"
        security_profile = os.getenv(
            "AMARYLLIS_SECURITY_PROFILE",
            "production",
        ).strip().lower()
        if security_profile not in {"production", "development"}:
            security_profile = "production"
        security_allow_insecure_modes = _parse_bool(
            os.getenv("AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES", "false")
        )
        tool_isolation_profile = os.getenv(
            "AMARYLLIS_TOOL_ISOLATION_PROFILE",
            "balanced",
        ).strip().lower()
        if tool_isolation_profile not in {"balanced", "strict"}:
            tool_isolation_profile = "balanced"
        plugin_signing_mode = os.getenv(
            "AMARYLLIS_PLUGIN_SIGNING_MODE",
            "strict",
        ).strip().lower()
        if plugin_signing_mode not in {"off", "warn", "strict"}:
            plugin_signing_mode = "strict"
        plugin_runtime_mode = os.getenv(
            "AMARYLLIS_PLUGIN_RUNTIME_MODE",
            "sandboxed",
        ).strip().lower()
        if plugin_runtime_mode not in {"sandboxed", "legacy"}:
            plugin_runtime_mode = "sandboxed"
        tool_sandbox_enabled = _parse_bool(os.getenv("AMARYLLIS_TOOL_SANDBOX_ENABLED", "true"))
        tool_sandbox_allow_network_tools = tuple(
            _csv_items(os.getenv("AMARYLLIS_TOOL_SANDBOX_ALLOW_NETWORK_TOOLS", "web_search"))
        )
        tool_sandbox_allowed_roots = tuple(
            _csv_items(
                os.getenv(
                    "AMARYLLIS_TOOL_SANDBOX_ALLOWED_ROOTS",
                    str(Path.cwd()),
                )
            )
        )
        auth_enabled = _parse_bool(os.getenv("AMARYLLIS_AUTH_ENABLED", "true"))
        auth_tokens = tuple(
            _parse_auth_tokens(
                raw=os.getenv("AMARYLLIS_AUTH_TOKENS", ""),
                single_token=os.getenv("AMARYLLIS_API_TOKEN", ""),
            )
        )
        config = cls(
            app_name="Amaryllis",
            app_version=os.getenv("AMARYLLIS_APP_VERSION", "0.1.0"),
            host=os.getenv("AMARYLLIS_HOST", "localhost"),
            port=int(os.getenv("AMARYLLIS_PORT", "8000")),
            support_dir=support_dir,
            models_dir=models_dir,
            data_dir=data_dir,
            backup_dir=backup_dir,
            evidence_dir=evidence_dir,
            plugins_dir=plugins_dir,
            database_path=database_path,
            vector_index_path=vector_index_path,
            telemetry_path=telemetry_path,
            observability_otel_enabled=_parse_bool(
                os.getenv("AMARYLLIS_OTEL_ENABLED", "true")
            ),
            observability_otlp_endpoint=(os.getenv("AMARYLLIS_OTEL_OTLP_ENDPOINT") or "").strip() or None,
            observability_slo_window_sec=max(
                60.0, float(os.getenv("AMARYLLIS_SLO_WINDOW_SEC", "3600"))
            ),
            observability_request_availability_target=min(
                0.9999,
                max(0.5, float(os.getenv("AMARYLLIS_SLO_REQUEST_AVAILABILITY_TARGET", "0.995"))),
            ),
            observability_request_latency_p95_ms_target=max(
                1.0, float(os.getenv("AMARYLLIS_SLO_REQUEST_LATENCY_P95_MS_TARGET", "1200"))
            ),
            observability_run_success_target=min(
                0.9999,
                max(0.5, float(os.getenv("AMARYLLIS_SLO_RUN_SUCCESS_TARGET", "0.98"))),
            ),
            observability_min_request_samples=max(
                1, int(os.getenv("AMARYLLIS_SLO_MIN_REQUEST_SAMPLES", "50"))
            ),
            observability_min_run_samples=max(
                1, int(os.getenv("AMARYLLIS_SLO_MIN_RUN_SAMPLES", "20"))
            ),
            observability_incident_cooldown_sec=max(
                5.0, float(os.getenv("AMARYLLIS_SLO_INCIDENT_COOLDOWN_SEC", "300"))
            ),
            backup_enabled=_parse_bool(os.getenv("AMARYLLIS_BACKUP_ENABLED", "true")),
            backup_interval_sec=max(
                30.0, float(os.getenv("AMARYLLIS_BACKUP_INTERVAL_SEC", "3600"))
            ),
            backup_retention_count=max(
                1, int(os.getenv("AMARYLLIS_BACKUP_RETENTION_COUNT", "120"))
            ),
            backup_retention_days=max(
                1, int(os.getenv("AMARYLLIS_BACKUP_RETENTION_DAYS", "30"))
            ),
            backup_verify_on_create=_parse_bool(
                os.getenv("AMARYLLIS_BACKUP_VERIFY_ON_CREATE", "true")
            ),
            backup_restore_drill_enabled=_parse_bool(
                os.getenv("AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED", "true")
            ),
            backup_restore_drill_interval_sec=max(
                300.0,
                float(
                    os.getenv(
                        "AMARYLLIS_BACKUP_RESTORE_DRILL_INTERVAL_SEC",
                        "86400",
                    )
                ),
            ),
            api_version=os.getenv("AMARYLLIS_API_VERSION", "v1").strip() or "v1",
            api_release_channel=api_release_channel,
            api_deprecation_sunset_days=max(
                7, int(os.getenv("AMARYLLIS_API_DEPRECATION_SUNSET_DAYS", "180"))
            ),
            api_compat_contract_path=api_compat_contract_path,
            default_provider=os.getenv("AMARYLLIS_DEFAULT_PROVIDER", "mlx"),
            default_model=os.getenv(
                "AMARYLLIS_DEFAULT_MODEL",
                "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
            ),
            ollama_base_url=os.getenv("AMARYLLIS_OLLAMA_URL", "http://localhost:11434"),
            enable_ollama_fallback=enable_ollama_fallback,
            openai_base_url=os.getenv("AMARYLLIS_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_api_key=(os.getenv("AMARYLLIS_OPENAI_API_KEY") or "").strip() or None,
            openai_api_key_rotated_at=(os.getenv("AMARYLLIS_OPENAI_API_KEY_ROTATED_AT") or "").strip() or None,
            openai_api_key_expires_at=(os.getenv("AMARYLLIS_OPENAI_API_KEY_EXPIRES_AT") or "").strip() or None,
            anthropic_base_url=os.getenv("AMARYLLIS_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").rstrip(
                "/"
            ),
            anthropic_api_key=(os.getenv("AMARYLLIS_ANTHROPIC_API_KEY") or "").strip() or None,
            anthropic_api_key_rotated_at=(os.getenv("AMARYLLIS_ANTHROPIC_API_KEY_ROTATED_AT") or "").strip() or None,
            anthropic_api_key_expires_at=(os.getenv("AMARYLLIS_ANTHROPIC_API_KEY_EXPIRES_AT") or "").strip() or None,
            openrouter_base_url=os.getenv("AMARYLLIS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            openrouter_api_key=(os.getenv("AMARYLLIS_OPENROUTER_API_KEY") or "").strip() or None,
            openrouter_api_key_rotated_at=(os.getenv("AMARYLLIS_OPENROUTER_API_KEY_ROTATED_AT") or "").strip() or None,
            openrouter_api_key_expires_at=(os.getenv("AMARYLLIS_OPENROUTER_API_KEY_EXPIRES_AT") or "").strip() or None,
            run_workers=max(1, int(os.getenv("AMARYLLIS_RUN_WORKERS", "2"))),
            run_max_attempts=max(1, int(os.getenv("AMARYLLIS_RUN_MAX_ATTEMPTS", "2"))),
            run_attempt_timeout_sec=max(5.0, float(os.getenv("AMARYLLIS_RUN_ATTEMPT_TIMEOUT_SEC", "180"))),
            run_lease_ttl_sec=max(
                10.0,
                float(
                    os.getenv(
                        "AMARYLLIS_RUN_LEASE_TTL_SEC",
                        str(max(10.0, float(os.getenv("AMARYLLIS_RUN_ATTEMPT_TIMEOUT_SEC", "180")) * 2.0 + 5.0)),
                    )
                ),
            ),
            run_retry_backoff_sec=max(0.0, float(os.getenv("AMARYLLIS_RUN_RETRY_BACKOFF_SEC", "0.3"))),
            run_retry_max_backoff_sec=max(0.0, float(os.getenv("AMARYLLIS_RUN_RETRY_MAX_BACKOFF_SEC", "2.0"))),
            run_retry_jitter_sec=max(0.0, float(os.getenv("AMARYLLIS_RUN_RETRY_JITTER_SEC", "0.15"))),
            run_budget_max_tokens=max(256, int(os.getenv("AMARYLLIS_RUN_BUDGET_MAX_TOKENS", "24000"))),
            run_budget_max_duration_sec=max(
                10.0, float(os.getenv("AMARYLLIS_RUN_BUDGET_MAX_DURATION_SEC", "300"))
            ),
            run_budget_max_tool_calls=max(1, int(os.getenv("AMARYLLIS_RUN_BUDGET_MAX_TOOL_CALLS", "8"))),
            run_budget_max_tool_errors=max(0, int(os.getenv("AMARYLLIS_RUN_BUDGET_MAX_TOOL_ERRORS", "3"))),
            automation_poll_sec=max(0.5, float(os.getenv("AMARYLLIS_AUTOMATION_POLL_SEC", "2"))),
            automation_batch_size=max(1, int(os.getenv("AMARYLLIS_AUTOMATION_BATCH_SIZE", "10"))),
            automation_escalation_warning=max(1, int(os.getenv("AMARYLLIS_AUTOMATION_ESCALATION_WARNING", "2"))),
            automation_escalation_critical=max(
                1, int(os.getenv("AMARYLLIS_AUTOMATION_ESCALATION_CRITICAL", "4"))
            ),
            automation_escalation_disable=max(1, int(os.getenv("AMARYLLIS_AUTOMATION_ESCALATION_DISABLE", "6"))),
            automation_lease_ttl_sec=max(5, int(os.getenv("AMARYLLIS_AUTOMATION_LEASE_TTL_SEC", "30"))),
            automation_backoff_base_sec=max(
                1.0, float(os.getenv("AMARYLLIS_AUTOMATION_BACKOFF_BASE_SEC", "5"))
            ),
            automation_backoff_max_sec=max(
                1.0, float(os.getenv("AMARYLLIS_AUTOMATION_BACKOFF_MAX_SEC", "300"))
            ),
            automation_circuit_failure_threshold=max(
                1, int(os.getenv("AMARYLLIS_AUTOMATION_CIRCUIT_FAILURE_THRESHOLD", "4"))
            ),
            automation_circuit_open_sec=max(
                1.0, float(os.getenv("AMARYLLIS_AUTOMATION_CIRCUIT_OPEN_SEC", "120"))
            ),
            task_max_duration_sec=max(10.0, float(os.getenv("AMARYLLIS_TASK_MAX_DURATION_SEC", "120"))),
            task_max_model_calls=max(1, int(os.getenv("AMARYLLIS_TASK_MAX_MODEL_CALLS", "6"))),
            task_max_prompt_chars=max(2000, int(os.getenv("AMARYLLIS_TASK_MAX_PROMPT_CHARS", "40000"))),
            task_max_tool_rounds=max(1, int(os.getenv("AMARYLLIS_TASK_MAX_TOOL_ROUNDS", "3"))),
            task_issue_parallel_workers=max(
                1, int(os.getenv("AMARYLLIS_TASK_ISSUE_PARALLEL_WORKERS", "2"))
            ),
            task_issue_timeout_sec=max(
                0.01, float(os.getenv("AMARYLLIS_TASK_ISSUE_TIMEOUT_SEC", "15"))
            ),
            task_verifier_enabled=_parse_bool(os.getenv("AMARYLLIS_TASK_VERIFIER_ENABLED", "true")),
            task_verifier_max_repair_attempts=max(
                0, int(os.getenv("AMARYLLIS_TASK_VERIFIER_MAX_REPAIR_ATTEMPTS", "1"))
            ),
            task_verifier_min_response_chars=max(
                1, int(os.getenv("AMARYLLIS_TASK_VERIFIER_MIN_RESPONSE_CHARS", "8"))
            ),
            task_artifact_quality_enabled=_parse_bool(
                os.getenv("AMARYLLIS_TASK_ARTIFACT_QUALITY_ENABLED", "true")
            ),
            task_artifact_quality_max_repair_attempts=max(
                0, int(os.getenv("AMARYLLIS_TASK_ARTIFACT_QUALITY_MAX_REPAIR_ATTEMPTS", "1"))
            ),
            task_step_verifier_enabled=_parse_bool(
                os.getenv("AMARYLLIS_TASK_STEP_VERIFIER_ENABLED", "true")
            ),
            task_step_max_retries_default=max(
                0, int(os.getenv("AMARYLLIS_TASK_STEP_MAX_RETRIES_DEFAULT", "1"))
            ),
            task_step_replan_max_attempts=max(
                0, int(os.getenv("AMARYLLIS_TASK_STEP_REPLAN_MAX_ATTEMPTS", "1"))
            ),
            memory_consolidation_enabled=memory_consolidation_enabled,
            memory_consolidation_interval_sec=max(
                30.0, float(os.getenv("AMARYLLIS_MEMORY_CONSOLIDATION_INTERVAL_SEC", "600"))
            ),
            memory_consolidation_semantic_limit=max(
                100, int(os.getenv("AMARYLLIS_MEMORY_CONSOLIDATION_SEMANTIC_LIMIT", "1000"))
            ),
            memory_consolidation_max_users_per_tick=max(
                1, int(os.getenv("AMARYLLIS_MEMORY_CONSOLIDATION_MAX_USERS_PER_TICK", "20"))
            ),
            memory_profile_decay_enabled=_parse_bool(
                os.getenv("AMARYLLIS_MEMORY_PROFILE_DECAY_ENABLED", "true")
            ),
            memory_profile_decay_half_life_days=max(
                1.0, float(os.getenv("AMARYLLIS_MEMORY_PROFILE_DECAY_HALF_LIFE_DAYS", "45"))
            ),
            memory_profile_decay_floor=max(
                0.0, min(1.0, float(os.getenv("AMARYLLIS_MEMORY_PROFILE_DECAY_FLOOR", "0.35")))
            ),
            memory_profile_decay_min_delta=max(
                0.0, float(os.getenv("AMARYLLIS_MEMORY_PROFILE_DECAY_MIN_DELTA", "0.05"))
            ),
            provider_retry_attempts=max(1, int(os.getenv("AMARYLLIS_PROVIDER_RETRY_ATTEMPTS", "2"))),
            provider_retry_backoff_sec=max(0.0, float(os.getenv("AMARYLLIS_PROVIDER_RETRY_BACKOFF_SEC", "0.5"))),
            provider_retry_jitter_sec=max(0.0, float(os.getenv("AMARYLLIS_PROVIDER_RETRY_JITTER_SEC", "0.2"))),
            provider_circuit_failure_threshold=max(
                1, int(os.getenv("AMARYLLIS_PROVIDER_CIRCUIT_FAILURE_THRESHOLD", "3"))
            ),
            provider_circuit_cooldown_sec=max(
                1.0, float(os.getenv("AMARYLLIS_PROVIDER_CIRCUIT_COOLDOWN_SEC", "20"))
            ),
            cloud_rate_window_sec=max(
                1.0, float(os.getenv("AMARYLLIS_CLOUD_RATE_WINDOW_SEC", "60"))
            ),
            cloud_rate_max_requests=max(
                1, int(os.getenv("AMARYLLIS_CLOUD_RATE_MAX_REQUESTS", "30"))
            ),
            cloud_budget_window_sec=max(
                60.0, float(os.getenv("AMARYLLIS_CLOUD_BUDGET_WINDOW_SEC", "3600"))
            ),
            cloud_budget_max_units=max(
                100, int(os.getenv("AMARYLLIS_CLOUD_BUDGET_MAX_UNITS", "400000"))
            ),
            security_profile=security_profile,
            security_allow_insecure_modes=security_allow_insecure_modes,
            auth_enabled=auth_enabled,
            auth_tokens=auth_tokens,
            chat_max_messages=max(1, int(os.getenv("AMARYLLIS_CHAT_MAX_MESSAGES", "80"))),
            chat_max_input_chars=max(2000, int(os.getenv("AMARYLLIS_CHAT_MAX_INPUT_CHARS", "50000"))),
            chat_max_tokens=max(64, int(os.getenv("AMARYLLIS_CHAT_MAX_TOKENS", "4096"))),
            tool_approval_enforcement=tool_approval_enforcement,
            tool_isolation_profile=tool_isolation_profile,
            tool_budget_window_sec=max(1.0, float(os.getenv("AMARYLLIS_TOOL_BUDGET_WINDOW_SEC", "60"))),
            tool_budget_max_calls_per_tool=max(
                1, int(os.getenv("AMARYLLIS_TOOL_BUDGET_MAX_CALLS_PER_TOOL", "12"))
            ),
            tool_budget_max_total_calls=max(
                1, int(os.getenv("AMARYLLIS_TOOL_BUDGET_MAX_TOTAL_CALLS", "40"))
            ),
            tool_budget_max_high_risk_calls=max(
                1, int(os.getenv("AMARYLLIS_TOOL_BUDGET_MAX_HIGH_RISK_CALLS", "4"))
            ),
            blocked_tools=blocked_tools,
            allowed_high_risk_tools=allowed_high_risk_tools,
            tool_python_exec_max_timeout_sec=max(
                1, int(os.getenv("AMARYLLIS_TOOL_PYTHON_EXEC_MAX_TIMEOUT_SEC", "10"))
            ),
            tool_python_exec_max_code_chars=max(
                100, int(os.getenv("AMARYLLIS_TOOL_PYTHON_EXEC_MAX_CODE_CHARS", "4000"))
            ),
            tool_filesystem_allow_write=_parse_bool(
                os.getenv("AMARYLLIS_TOOL_FILESYSTEM_ALLOW_WRITE", "true")
            ),
            tool_sandbox_enabled=tool_sandbox_enabled,
            tool_sandbox_timeout_sec=max(
                1, int(os.getenv("AMARYLLIS_TOOL_SANDBOX_TIMEOUT_SEC", "12"))
            ),
            tool_sandbox_max_cpu_sec=max(
                1, int(os.getenv("AMARYLLIS_TOOL_SANDBOX_MAX_CPU_SEC", "6"))
            ),
            tool_sandbox_max_memory_mb=max(
                64, int(os.getenv("AMARYLLIS_TOOL_SANDBOX_MAX_MEMORY_MB", "512"))
            ),
            tool_sandbox_allow_network_tools=tool_sandbox_allow_network_tools,
            tool_sandbox_allowed_roots=tool_sandbox_allowed_roots,
            plugin_signing_key=(os.getenv("AMARYLLIS_PLUGIN_SIGNING_KEY") or "").strip() or None,
            plugin_signing_key_rotated_at=(os.getenv("AMARYLLIS_PLUGIN_SIGNING_KEY_ROTATED_AT") or "").strip() or None,
            plugin_signing_key_expires_at=(os.getenv("AMARYLLIS_PLUGIN_SIGNING_KEY_EXPIRES_AT") or "").strip() or None,
            plugin_signing_mode=plugin_signing_mode,
            plugin_runtime_mode=plugin_runtime_mode,
            mcp_endpoints=mcp_endpoints,
            mcp_timeout_sec=max(1.0, float(os.getenv("AMARYLLIS_MCP_TIMEOUT_SEC", "10"))),
            mcp_failure_threshold=max(1, int(os.getenv("AMARYLLIS_MCP_FAILURE_THRESHOLD", "2"))),
            mcp_quarantine_sec=max(1.0, float(os.getenv("AMARYLLIS_MCP_QUARANTINE_SEC", "60"))),
            compliance_secret_rotation_max_age_days=max(
                1, int(os.getenv("AMARYLLIS_SECRET_ROTATION_MAX_AGE_DAYS", "90"))
            ),
            compliance_secret_expiry_warning_days=max(
                1, int(os.getenv("AMARYLLIS_SECRET_EXPIRY_WARNING_DAYS", "14"))
            ),
            compliance_identity_rotation_max_age_days=max(
                1, int(os.getenv("AMARYLLIS_IDENTITY_ROTATION_MAX_AGE_DAYS", "30"))
            ),
            compliance_access_review_max_age_days=max(
                1, int(os.getenv("AMARYLLIS_ACCESS_REVIEW_MAX_AGE_DAYS", "30"))
            ),
            identity_path=identity_path,
        )
        config._validate_security_configuration()
        return config

    def ensure_directories(self) -> None:
        self.support_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)

    def _validate_security_configuration(self) -> None:
        if self.security_profile != "production":
            return
        errors: list[str] = []
        if self.security_allow_insecure_modes:
            errors.append("AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES must be false in production")
        if not self.auth_enabled:
            errors.append("AMARYLLIS_AUTH_ENABLED must be true in production")
        if not self.auth_tokens:
            errors.append("At least one auth token must be configured in production")
        if self.tool_approval_enforcement != "strict":
            errors.append("AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT must be strict in production")
        if not self.tool_sandbox_enabled:
            errors.append("AMARYLLIS_TOOL_SANDBOX_ENABLED must be true in production")
        if self.plugin_signing_mode != "strict":
            errors.append("AMARYLLIS_PLUGIN_SIGNING_MODE must be strict in production")
        if self.plugin_runtime_mode != "sandboxed":
            errors.append("AMARYLLIS_PLUGIN_RUNTIME_MODE must be sandboxed in production")
        if errors:
            raise AppConfigError("Invalid production security configuration: " + "; ".join(errors))


def _csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _parse_auth_tokens(*, raw: str, single_token: str) -> list[AuthTokenConfig]:
    items: list[AuthTokenConfig] = []
    seen_tokens: set[str] = set()

    def add(token: str, user_id: str, scopes: list[str] | tuple[str, ...] | set[str]) -> None:
        normalized_token = str(token or "").strip()
        normalized_user = str(user_id or "").strip()
        normalized_scopes = tuple(
            sorted(
                {
                    str(scope or "").strip().lower()
                    for scope in scopes
                    if str(scope or "").strip()
                }
            )
        )
        if not normalized_token or not normalized_user:
            return
        if not normalized_scopes:
            scope_tuple = ("user",)
        else:
            scope_tuple = normalized_scopes
        if normalized_token in seen_tokens:
            return
        seen_tokens.add(normalized_token)
        items.append(
            AuthTokenConfig(
                token=normalized_token,
                user_id=normalized_user,
                scopes=scope_tuple,
            )
        )

    trimmed = str(raw or "").strip()
    if trimmed:
        parsed_json = None
        if trimmed.startswith("{"):
            try:
                parsed_json = json.loads(trimmed)
            except Exception:
                parsed_json = None

        if isinstance(parsed_json, dict):
            for token, value in parsed_json.items():
                token_str = str(token or "").strip()
                if not token_str:
                    continue
                if isinstance(value, dict):
                    user_id = str(
                        value.get("user_id")
                        or value.get("subject")
                        or value.get("actor")
                        or ""
                    ).strip()
                    scopes_raw = value.get("scopes")
                    if isinstance(scopes_raw, list):
                        scopes = [str(scope) for scope in scopes_raw]
                    elif isinstance(scopes_raw, str):
                        scopes = [part for part in scopes_raw.replace(",", "|").split("|")]
                    else:
                        scopes = ["user"]
                    add(token_str, user_id, scopes)
                    continue
                if isinstance(value, str):
                    add(token_str, value, ["user"])
                    continue
                add(token_str, "user", ["user"])
        else:
            for entry in trimmed.split(","):
                part = str(entry or "").strip()
                if not part:
                    continue
                token_segment, _, rest = part.partition(":")
                token_str = token_segment.strip()
                user_id = "user"
                scopes = ["user"]
                if rest:
                    user_segment, _, scope_segment = rest.partition(":")
                    if user_segment.strip():
                        user_id = user_segment.strip()
                    if scope_segment.strip():
                        scopes = [item for item in scope_segment.replace(",", "|").split("|")]
                add(token_str, user_id, scopes)

    single = str(single_token or "").strip()
    if single:
        add(single, "admin", ["admin", "user"])

    return items
