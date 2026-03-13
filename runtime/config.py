from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    app_name: str
    host: str
    port: int
    support_dir: Path
    models_dir: Path
    data_dir: Path
    plugins_dir: Path
    database_path: Path
    vector_index_path: Path
    telemetry_path: Path
    default_provider: str
    default_model: str
    ollama_base_url: str
    enable_ollama_fallback: bool
    openai_base_url: str
    openai_api_key: str | None
    anthropic_base_url: str
    anthropic_api_key: str | None
    openrouter_base_url: str
    openrouter_api_key: str | None
    run_workers: int
    run_max_attempts: int
    run_attempt_timeout_sec: float
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
    plugin_signing_key: str | None
    plugin_signing_mode: str
    mcp_endpoints: tuple[str, ...]
    mcp_timeout_sec: float
    mcp_failure_threshold: int
    mcp_quarantine_sec: float
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
            "prompt_and_allow",
        ).strip().lower()
        if tool_approval_enforcement not in {"strict", "prompt_and_allow"}:
            tool_approval_enforcement = "prompt_and_allow"
        tool_isolation_profile = os.getenv(
            "AMARYLLIS_TOOL_ISOLATION_PROFILE",
            "balanced",
        ).strip().lower()
        if tool_isolation_profile not in {"balanced", "strict"}:
            tool_isolation_profile = "balanced"
        plugin_signing_mode = os.getenv(
            "AMARYLLIS_PLUGIN_SIGNING_MODE",
            "warn",
        ).strip().lower()
        if plugin_signing_mode not in {"off", "warn", "strict"}:
            plugin_signing_mode = "warn"

        return cls(
            app_name="Amaryllis",
            host=os.getenv("AMARYLLIS_HOST", "localhost"),
            port=int(os.getenv("AMARYLLIS_PORT", "8000")),
            support_dir=support_dir,
            models_dir=models_dir,
            data_dir=data_dir,
            plugins_dir=plugins_dir,
            database_path=database_path,
            vector_index_path=vector_index_path,
            telemetry_path=telemetry_path,
            default_provider=os.getenv("AMARYLLIS_DEFAULT_PROVIDER", "mlx"),
            default_model=os.getenv(
                "AMARYLLIS_DEFAULT_MODEL",
                "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
            ),
            ollama_base_url=os.getenv("AMARYLLIS_OLLAMA_URL", "http://localhost:11434"),
            enable_ollama_fallback=enable_ollama_fallback,
            openai_base_url=os.getenv("AMARYLLIS_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_api_key=(os.getenv("AMARYLLIS_OPENAI_API_KEY") or "").strip() or None,
            anthropic_base_url=os.getenv("AMARYLLIS_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").rstrip(
                "/"
            ),
            anthropic_api_key=(os.getenv("AMARYLLIS_ANTHROPIC_API_KEY") or "").strip() or None,
            openrouter_base_url=os.getenv("AMARYLLIS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            openrouter_api_key=(os.getenv("AMARYLLIS_OPENROUTER_API_KEY") or "").strip() or None,
            run_workers=max(1, int(os.getenv("AMARYLLIS_RUN_WORKERS", "2"))),
            run_max_attempts=max(1, int(os.getenv("AMARYLLIS_RUN_MAX_ATTEMPTS", "2"))),
            run_attempt_timeout_sec=max(5.0, float(os.getenv("AMARYLLIS_RUN_ATTEMPT_TIMEOUT_SEC", "180"))),
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
            plugin_signing_key=(os.getenv("AMARYLLIS_PLUGIN_SIGNING_KEY") or "").strip() or None,
            plugin_signing_mode=plugin_signing_mode,
            mcp_endpoints=mcp_endpoints,
            mcp_timeout_sec=max(1.0, float(os.getenv("AMARYLLIS_MCP_TIMEOUT_SEC", "10"))),
            mcp_failure_threshold=max(1, int(os.getenv("AMARYLLIS_MCP_FAILURE_THRESHOLD", "2"))),
            mcp_quarantine_sec=max(1.0, float(os.getenv("AMARYLLIS_MCP_QUARANTINE_SEC", "60"))),
            identity_path=identity_path,
        )

    def ensure_directories(self) -> None:
        self.support_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)


def _csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str) -> bool:
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}
