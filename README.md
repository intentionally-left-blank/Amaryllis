# Amaryllis

Amaryllis is an open-source local AI runtime and native macOS app.

It acts as a **local AI brain node**:
- runs local models
- supports agent execution
- provides tool calling
- stores memory
- exposes OpenAI-compatible API
- ships with a native SwiftUI desktop interface

This MVP is intentionally simple and modular, so it can evolve into a richer cognitive architecture later.

## Privacy and Anonymity

- local telemetry is default; optional OpenTelemetry export can be enabled explicitly
- no personal paths or machine-specific identifiers in repository files
- local-first runtime, data stays on your machine unless tools/providers call external services

## MVP Scope

Implemented in this version:
- FastAPI backend runtime
- native macOS UI (`SwiftUI`) with dark amaryllis theme
- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- auth enabled by default with scoped access (`user`, `admin`, `service`)
- model manager with MLX primary provider, Ollama fallback, and optional cloud providers (OpenAI / Anthropic / OpenRouter)
- model APIs: list/download/load/capabilities
- agent APIs: create/list/chat
- memory layer v2 foundation: working + episodic + semantic + profile memory
- SQLite persistence
- vector search via FAISS (with local fallback behavior)
- tool registry/executor with builtin tools
- plugin discovery from `plugins/`
- sequential task loop: meta-controller -> planner -> reasoning -> tools -> response
- local runtime controls from the desktop app (start/stop/check)
- one-click `Quick Setup` in desktop app (runtime start + API check + model readiness)
- streaming chat UI
- simplified chat controls (model/stream/tools first, advanced routing hidden behind `Advanced`)
- model load/download progress indicators
- simplified models flow with searchable `Simple Library` and one-click `Install & Use`
- persistent local chat history (multi-chat sessions) in macOS app
- Agents tab automation UI with `watch_fs` + inbox read/unread controls
- centralized structured API errors (`error.type`, `error.message`, `error.request_id`)
- strict owner checks across API + background flows (anti-IDOR/BOLA for multi-tenant access)
- provider diagnostics endpoint: `GET /health/providers`
- SQLite migration framework (`schema_migrations`)
- local structured telemetry (`telemetry.jsonl`)
- OpenTelemetry-ready tracing/log correlation (`trace_id`) with graceful fallback if OTel deps are missing
- SRE observability endpoints (`/service/observability/slo`, `/service/observability/incidents`, `/service/observability/metrics`)
- automatic incident detection from SLO breaches (availability, latency p95, run success)
- API lifecycle policy with version headers and legacy deprecation headers (`Deprecation`, `Sunset`)
- versioned API aliases for core routes under `/v1/*` with compatibility contract gate
- release gate assets: compatibility script, canary smoke script, disaster-recovery gate, compliance gate, rollback playbook
- lease/CAS ownership for agent runs (single-owner execution under concurrent workers)
- mission simulation mode before apply with risk/rollback preview and signed dry-run receipt (`POST /agents/{agent_id}/runs/simulate`)
- mission template catalog for automation planning (`GET /automations/mission/templates`) with defaults (`code_health`, `security_audit`, `release_guard`, `runtime_watchdog`)
- mission policy catalog for per-automation SLO overlays (`GET /automations/mission/policies`) with enforcement profiles (`balanced`, `strict`, `watchdog`, `release`)
- plugin compatibility contract + capability isolation policy (`compat` + `capabilities` manifest fields, fail-fast discovery validation)
- release/nightly public quality artifacts: release quality dashboard snapshot + mission success/recovery report pack
- compact run diagnostics endpoint for mission postmortem (`GET /agents/runs/{run_id}/diagnostics`)
- voice push-to-talk session contract with explicit state transitions (`created -> listening -> stopping -> stopped`)
- pluggable local STT adapter layer (`whisper_python` backend + graceful unavailable mode)
- typed planner step execution with step contracts (pre/post conditions), verifier, retry and replan
- modular step executor package (`tasks/execution/step_executors.py`) separated from run orchestration
- production-grade backup and DR foundation (scheduled backups, retention, verification, restore drills)
- compliance/security operations baseline: secret inventory posture, access reviews, incident response workflow, signed audit evidence export

Out of scope for MVP:
- distributed execution
- multi-node orchestration
- full production hardening

## Target Platform

Primary target:
- macOS (Apple Silicon)
- Python 3.11+

Model storage location:
- `~/Library/Application Support/amaryllis/models/`

Data storage location:
- `~/Library/Application Support/amaryllis/data/`

Local telemetry log:
- `~/Library/Application Support/amaryllis/data/telemetry.jsonl`

Backup storage location:
- `~/Library/Application Support/amaryllis/backups/`

Service observability endpoints:
- `GET /service/observability/slo`
- `GET /service/observability/incidents`
- `GET /service/observability/metrics`

Service API lifecycle endpoint:
- `GET /service/api/lifecycle`

Service backup/DR endpoints:
- `GET /service/backup/status`
- `GET /service/backup/backups`
- `POST /service/backup/run`
- `POST /service/backup/verify`
- `POST /service/backup/restore-drill`
- `POST /service/runs/kill-switch`

## Project Structure

```text
.
├── agents
│   ├── agent.py
│   ├── agent_manager.py
│   └── agent_run_manager.py
├── automation
│   ├── automation_scheduler.py
│   └── schedule.py
├── api
│   ├── agent_api.py
│   ├── automation_api.py
│   ├── backup_api.py
│   ├── chat_api.py
│   ├── inbox_api.py
│   ├── memory_api.py
│   ├── model_api.py
│   ├── security_api.py
│   └── tool_api.py
├── controller
│   └── meta_controller.py
├── memory
│   ├── extraction_service.py
│   ├── eval_suite.py
│   ├── episodic_memory.py
│   ├── memory_manager.py
│   ├── models.py
│   ├── semantic_memory.py
│   ├── user_memory.py
│   └── working_memory.py
├── models
│   ├── model_manager.py
│   └── providers
│       ├── mlx_provider.py
│       ├── anthropic_provider.py
│       ├── openai_provider.py
│       ├── openrouter_provider.py
│       └── ollama_provider.py
├── macos
│   └── AmaryllisApp
│       ├── Package.swift
│       ├── Sources/AmaryllisApp
│       │   ├── AmaryllisMacApp.swift
│       │   ├── Core
│       │   ├── Models
│       │   ├── Services
│       │   └── Views
│       └── scripts
│           └── build_app.sh
├── planner
│   └── planner.py
├── plugins
│   └── .gitkeep
├── runtime
│   ├── auth.py
│   ├── backup.py
│   ├── compliance.py
│   ├── config.py
│   ├── security.py
│   └── server.py
├── scripts
│   ├── disaster_recovery
│   │   ├── backup_now.py
│   │   ├── kill_switch_runs.py
│   │   ├── restore_drill.py
│   │   └── restore_from_archive.py
│   ├── release
│   │   ├── api_compat_gate.py
│   │   ├── canary_smoke.py
│   │   ├── compliance_ops_gate.py
│   │   ├── disaster_recovery_gate.py
│   │   └── rollback_local.sh
│   └── security
│       ├── compliance_check.py
│       ├── export_audit_evidence.py
│       └── policy_check.py
├── storage
│   ├── database.py
│   ├── migrations.py
│   └── vector_store.py
├── tasks
│   ├── execution
│   │   └── step_executors.py
│   ├── step_registry.py
│   └── task_executor.py
├── tests
│   ├── test_agent_run_manager.py
│   ├── test_automation_schedule.py
│   ├── test_automation_scheduler.py
│   ├── test_database_persistence_hardening.py
│   ├── test_memory_manager.py
│   ├── test_memory_quality_eval.py
│   ├── test_model_routing.py
│   ├── test_security_compliance_api.py
│   ├── test_security_manager.py
│   ├── test_task_executor.py
│   ├── test_tool_sandbox.py
│   └── test_tools_mcp.py
├── tools
│   ├── builtin_tools
│   │   ├── filesystem.py
│   │   ├── python_exec.py
│   │   └── web_search.py
│   ├── mcp_client_registry.py
│   ├── permission_manager.py
│   ├── policy.py
│   ├── sandbox_runner.py
│   ├── sandbox_worker.py
│   ├── sandboxed_tools.py
│   ├── tool_executor.py
│   └── tool_registry.py
├── voice
│   ├── session_manager.py
│   └── stt_adapter.py
├── LICENSE
├── README.md
└── requirements.txt
```

## Install

One command (from GitHub):

```bash
curl -fsSL https://raw.githubusercontent.com/intentionally-left-blank/Amaryllis/main/scripts/install_macos.sh | bash
```

One command (inside cloned repo):

```bash
./scripts/install_macos.sh
```

Linux runtime install (inside cloned repo):

```bash
./scripts/install_linux.sh
```

Render runtime lifecycle manifests (Phase 3 contract slice):

```bash
python3 scripts/runtime/render_service_manifest.py --target linux-systemd
python3 scripts/runtime/render_service_manifest.py --target macos-launchd
python3 scripts/runtime/manage_service.py install --target linux-systemd --dry-run
python3 scripts/runtime/manage_service.py status --target linux-systemd --dry-run
python3 scripts/runtime/manage_service.py rollback --target linux-systemd --dry-run
python3 scripts/release/runtime_lifecycle_smoke_gate.py --output artifacts/runtime-lifecycle-smoke-report.json
```

Manual backend setup:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

Reproducible bootstrap path (recommended for clean machine / CI parity):

```bash
./scripts/bootstrap/reproducible_local_bootstrap.sh
```

Deterministic dependency path (manual):

```bash
python scripts/release/check_toolchain_drift.py
pip install -r requirements.lock
python scripts/release/check_dependency_drift.py
python scripts/release/check_runtime_profile_drift.py
python scripts/eval/run_golden_tasks.py --validate-only
python scripts/release/check_eval_replay_determinism.py
```

Reference:
- `docs/reproducible-bootstrap.md`
- `docs/runtime-profiles.md`
- `docs/toolchain-manifest.md`
- `docs/eval-replay-determinism.md`
- `docs/release-provenance-sbom.md`
- `docs/mission-simulation-mode.md`
- `docs/agent-run-interaction-modes.md`
- `docs/mission-planner.md`
- `docs/flow-session-contract.md`
- `docs/automation-mission-policy.md`
- `docs/linux-desktop-action-adapters.md`
- `docs/supervisor-task-graph-contract.md`
- `docs/plugin-compat-contract.md`
- `docs/plugin-capability-policy.md`
- `docs/dynamic-mission-budgets.md`
- `docs/release-quality-dashboard.md`
- `docs/mission-success-recovery-report-pack.md`
- `docs/linux-runtime-installer.md`
- `docs/linux-release-channels.md`
- `docs/runtime-lifecycle-contract.md`
- `docs/jarvis-phase3-backlog.md`

## Run

```bash
uvicorn runtime.server:app --host localhost --port 8000 --reload
```

Authentication is enabled by default.

Only `GET /health` is public. All other endpoints require:

```bash
export AMARYLLIS_TOKEN="replace_me"
export AUTH_HEADER="Authorization: Bearer ${AMARYLLIS_TOKEN}"
```

Scopes:
- `user`: regular API access
- `admin`: security/debug endpoints and elevated actions
- `service`: `/service/*` endpoints (also allowed for `admin`)

Health check:

```bash
curl http://localhost:8000/health
```

Provider health:

```bash
curl -H "$AUTH_HEADER" http://localhost:8000/health/providers
```

Service health:

```bash
curl -H "$AUTH_HEADER" http://localhost:8000/service/health
```

## Native macOS App (.app)

Prerequisites:
- Xcode Command Line Tools installed (`xcode-select --install`)
- Xcode license accepted (`sudo xcodebuild -license accept`)

Build:

```bash
cd macos/AmaryllisApp
./scripts/build_app.sh
```

Result:

```text
macos/AmaryllisApp/dist/Amaryllis.app
```

Run:

```bash
open macos/AmaryllisApp/dist/Amaryllis.app
```

Build `.dmg`:

```bash
./scripts/build_dmg.sh
```

Output:

```text
macos/AmaryllisApp/dist/Amaryllis.dmg
```

First launch (recommended):
1. Open app.
2. Go to `Settings` once and set:
   - `API Endpoint` (default `http://localhost:8000`)
   - `Runtime Directory` (repository root)
3. Press `Quick Setup` in top bar.
4. If no model is active yet, open `Models` and press `Install & Use` on a suggested model.
5. Start chatting in `Chat`.

Notes:
- API keys entered in app settings are stored in macOS Keychain
- optional cloud providers:
  - OpenAI (`https://api.openai.com/v1`)
  - OpenRouter (`https://openrouter.ai/api/v1`)
- in `Agents` tab, configure interval/hourly/weekly/watcher automations and process inbox alerts
- `Settings` also contains advanced runtime/tools/memory debug controls
- desktop UI theme uses retro terminal styling (80s-inspired) with bundled `OlivettiThin 9x14` bitmap font

Font attribution:
- see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

Chat tab behavior:
- create multiple chats (`New Chat`)
- switch chats from the chat selector
- full chat history is saved automatically and restored after restart
- default controls are simplified: `Model`, `Stream`, `Tools`
- routing/provider policy controls are available under `Advanced`
- if runtime/model is not ready, use `Quick Setup` card in Chat

Local chat file:
- `~/Library/Application Support/amaryllis/chat_sessions.json`

API note:
- in all examples below (except `GET /health`), add `-H "$AUTH_HEADER"` if auth is enabled (default)

## Model Management API

### List models

```bash
curl http://localhost:8000/models
```

### Provider capability matrix

```bash
curl http://localhost:8000/models/capabilities
```

### Model capability matrix (provider-agnostic)

```bash
curl "http://localhost:8000/models/capability-matrix?include_suggested=true&limit_per_provider=120"
```

### Resolve best route for request policy

```bash
curl -X POST http://localhost:8000/models/route \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "coding",
    "require_stream": true,
    "prefer_local": true,
    "include_suggested": false
  }'
```

### Debug failover and session route pins

```bash
curl "http://localhost:8000/debug/models/failover?session_id=chat-001&limit=100"
```

### Download model (MLX)

```bash
curl -X POST http://localhost:8000/models/download \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "provider": "mlx"
  }'
```

Tip: `/models` returns `suggested` lists for `mlx` and `ollama`; desktop UI uses them in `Models -> Simple Library` with one-click `Install & Use` (download + activate).

### Load model

```bash
curl -X POST http://localhost:8000/models/load \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "provider": "mlx"
  }'
```

### Load remote OpenAI-compatible model (optional)

```bash
curl -X POST http://localhost:8000/models/load \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "gpt-4o-mini",
    "provider": "openai"
  }'
```

### Load remote OpenRouter model (optional)

```bash
curl -X POST http://localhost:8000/models/load \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "openai/gpt-4o-mini",
    "provider": "openrouter"
  }'
```

### Load remote Anthropic model (optional)

```bash
curl -X POST http://localhost:8000/models/load \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "claude-3-5-sonnet-latest",
    "provider": "anthropic"
  }'
```

## OpenAI-Compatible Chat API

`POST /v1/chat/completions`

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "messages": [
      {"role": "system", "content": "You are a concise assistant."},
      {"role": "user", "content": "Explain what Amaryllis is."}
    ],
    "stream": false
  }'
```

Auto-routing mode (no fixed `provider/model`):

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Refactor this Python snippet for readability."}],
    "routing": {
      "mode": "coding",
      "require_stream": false,
      "prefer_local": true
    },
    "stream": false
  }'
```

Streaming mode:

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

Tool-call loop mode (non-stream) with permission resume:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "messages": [{"role": "user", "content": "List files in my home folder"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "filesystem",
          "description": "Read/write files",
          "parameters": {
            "type": "object",
            "properties": {
              "action": {"type": "string"},
              "path": {"type": "string"}
            },
            "required": ["action", "path"]
          }
        }
      }
    ],
    "permission_ids": [],
    "stream": false
  }'
```

Notes:
- `session_id` can be provided in chat requests for session-level route pinning
- non-stream responses include `tool_events` trace with status and duration
- when a tool requires approval, `tool_events` includes `permission_prompt_id`
- after approving prompt(s), resend with `permission_ids` to continue tool execution
- `routing` in response now includes `final` target and `failover_events` diagnostics when fallback happens

Session-pinned chat example:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "chat-001",
    "messages": [{"role": "user", "content": "Continue previous context"}],
    "routing": {"mode": "balanced", "require_stream": false},
    "stream": false
  }'
```

## Voice API (PTT Foundation)

Start voice session:

```bash
curl -X POST http://localhost:8000/voice/sessions/start \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "mode": "ptt",
    "sample_rate_hz": 16000,
    "input_device": "default"
  }'
```

Get voice session:

```bash
curl "http://localhost:8000/voice/sessions/<session_id>"
```

List voice sessions (owner scoped):

```bash
curl "http://localhost:8000/voice/sessions?user_id=user-001&state=listening&limit=50"
```

Stop voice session:

```bash
curl -X POST "http://localhost:8000/voice/sessions/<session_id>/stop" \
  -H "Content-Type: application/json" \
  -d '{"reason":"user_done"}'
```

Check STT adapter health:

```bash
curl "http://localhost:8000/voice/stt/health"
```

Transcribe with STT adapter:

```bash
curl -X POST "http://localhost:8000/voice/stt/transcribe" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"user-001",
    "audio_path":"/absolute/path/to/audio.wav",
    "language":"en"
  }'
```

Transcribe base64 audio bound to existing voice session:

```bash
curl -X POST "http://localhost:8000/voice/stt/transcribe" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"<session_id>",
    "audio_base64":"<base64-audio>",
    "language":"en"
  }'
```

Voice STT env vars:
- `AMARYLLIS_VOICE_STT_BACKEND` (default `whisper_python`; supported: `whisper_python`, `none`)
- `AMARYLLIS_VOICE_STT_MODEL` (default `base`)

## Unified Flow API (Text/Voice/Visual)

Get flow contract:

```bash
curl "http://localhost:8000/flow/sessions/contract"
```

Start unified session:

```bash
curl -X POST http://localhost:8000/flow/sessions/start \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "channels": ["text", "voice", "visual"],
    "initial_state": "listening",
    "metadata": {"source": "desktop-ui"}
  }'
```

Transition session state:

```bash
curl -X POST "http://localhost:8000/flow/sessions/<session_id>/transition" \
  -H "Content-Type: application/json" \
  -d '{
    "to_state": "planning",
    "reason": "user_requested_plan"
  }'
```

Record channel activity:

```bash
curl -X POST "http://localhost:8000/flow/sessions/<session_id>/activity" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "text",
    "event": "prompt_submitted"
  }'
```

List sessions (owner scoped):

```bash
curl "http://localhost:8000/flow/sessions?user_id=user-001&state=planning&limit=50"
```

## Agent API

### Create agent

```bash
curl -X POST http://localhost:8000/agents/create \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Research Agent",
    "system_prompt": "You are a practical research assistant.",
    "model": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "tools": ["web_search", "filesystem"],
    "user_id": "user-001"
  }'
```

### List agents

```bash
curl "http://localhost:8000/agents?user_id=user-001"
```

### Chat with agent

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "session_id": "session-001",
    "message": "Find 3 sources about MLX and summarize them."
  }'
```

### Work Mode: interaction modes contract

```bash
curl "http://localhost:8000/agents/runs/interaction-modes"
```

### Work Mode: dispatch (explicit plan-vs-execute)

Plan first (dry-run, no execution):

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/runs/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "session_id": "session-001",
    "message": "Investigate errors and propose remediation plan",
    "interaction_mode": "plan",
    "max_attempts": 3,
    "budget": {
      "max_tokens": 18000,
      "max_duration_sec": 240,
      "max_tool_calls": 8,
      "max_tool_errors": 2
    }
  }'
```

Execute now (create async run immediately):

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/runs/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "session_id": "session-001",
    "message": "Investigate errors and propose remediation plan",
    "interaction_mode": "execute",
    "max_attempts": 3,
    "budget": {
      "max_tokens": 18000,
      "max_duration_sec": 240,
      "max_tool_calls": 8,
      "max_tool_errors": 2
    }
  }'
```

### Supervisor: multi-agent DAG graph (bounded orchestration skeleton)

Check contract:

```bash
curl "http://localhost:8000/supervisor/graphs/contract"
```

Create graph:

```bash
curl -X POST http://localhost:8000/supervisor/graphs/create \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "objective": "Triage, fix, and verify production incident",
    "nodes": [
      {
        "node_id": "triage",
        "agent_id": "<triage_agent_id>",
        "message": "Analyze incident and identify probable root cause"
      },
      {
        "node_id": "fix",
        "agent_id": "<fix_agent_id>",
        "message": "Prepare remediation plan and patch proposal",
        "depends_on": ["triage"]
      },
      {
        "node_id": "verify",
        "agent_id": "<verify_agent_id>",
        "message": "Validate remediation and summarize residual risk",
        "depends_on": ["fix"]
      }
    ]
  }'
```

Launch and tick graph:

```bash
curl -X POST "http://localhost:8000/supervisor/graphs/<graph_id>/launch" \
  -H "Content-Type: application/json" \
  -d '{"session_id":"session-001"}'

curl -X POST "http://localhost:8000/supervisor/graphs/<graph_id>/tick" \
  -H "Content-Type: application/json" \
  -d '{"noop": true}'
```

Supervisor checkpoints are persisted in SQLite. After runtime restart, existing graphs are auto-hydrated and can continue from the next `tick` without losing dependency/run linkage.

### Work Mode: create async run

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/runs \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "session_id": "session-001",
    "message": "Find 3 sources about MLX and summarize them.",
    "max_attempts": 2
  }'
```

Work Mode with explicit run budgets:

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/runs \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "session_id": "session-001",
    "message": "Investigate errors and summarize remediation plan",
    "max_attempts": 3,
    "budget": {
      "max_tokens": 18000,
      "max_duration_sec": 240,
      "max_tool_calls": 8,
      "max_tool_errors": 2
    }
  }'
```

Work Mode simulation (dry-run plan/risk preview before execution):

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/runs/simulate \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "session_id": "session-001",
    "message": "Investigate errors and summarize remediation plan",
    "max_attempts": 3,
    "budget": {
      "max_tokens": 18000,
      "max_duration_sec": 240,
      "max_tool_calls": 8,
      "max_tool_errors": 2
    }
  }'
```

Simulation response includes:
- `simulation.plan`: step preview with `risk_tags` and `rollback_hints`
- `simulation.tools`: tool risk/approval preview
- `simulation.run_preview`: normalized attempts/budget used for apply
- `simulation.apply_hint`: ready payload for `POST /agents/<agent_id>/runs`
- `dry_run_receipt`: signed action receipt for audit trail

Budget guardrail escalation (work mode):
- first breach pauses mission (`stop_reason=budget_guardrail_paused`)
- repeated breach escalates to agent-scope kill switch (`stop_reason=budget_guardrail_kill_switch`)

### Work Mode: list runs for agent

```bash
curl "http://localhost:8000/agents/<agent_id>/runs?user_id=user-001&limit=20"
```

Filter by status:

```bash
curl "http://localhost:8000/agents/<agent_id>/runs?user_id=user-001&status=running&limit=20"
```

### Work Mode: get run by id

```bash
curl "http://localhost:8000/agents/runs/<run_id>"
```

### Work Mode: replay run timeline/attempts

```bash
curl "http://localhost:8000/agents/runs/<run_id>/replay"
```

Filtered replay (server-side timeline filtering for HUD):

```bash
curl "http://localhost:8000/agents/runs/<run_id>/replay?stage=error&attempt=1&timeline_limit=50"
```

Replay presets (`errors`, `tools`, `verify`) and status/failure filters:

```bash
curl "http://localhost:8000/agents/runs/<run_id>/replay?preset=errors&failure_class=timeout&retryable=true&timeline_limit=100"
curl "http://localhost:8000/agents/runs/<run_id>/replay?preset=tools&status=failed&timeline_limit=100"
```

### Work Mode: stream run events (SSE for HUD)

```bash
curl -N "http://localhost:8000/agents/runs/<run_id>/events?poll_interval_ms=250&timeout_sec=30"
```

### Work Mode: run diagnostics summary (warnings + actions)

```bash
curl "http://localhost:8000/agents/runs/<run_id>/diagnostics"
```

### Work Mode: export run diagnostics package (replay + evidence bundle)

```bash
curl "http://localhost:8000/agents/runs/<run_id>/diagnostics/package"
```

### Work Mode: list issue states for run

```bash
curl "http://localhost:8000/agents/runs/<run_id>/issues?limit=200"
```

### Work Mode: list persisted issue artifacts for run

```bash
curl "http://localhost:8000/agents/runs/<run_id>/artifacts?limit=500"
curl "http://localhost:8000/agents/runs/<run_id>/artifacts?issue_id=plan_step:1&limit=100"
```

### Work Mode: debug run health/SLO snapshot

```bash
curl "http://localhost:8000/debug/agents/runs/health?user_id=user-001&limit=200"
```

### Work Mode: cancel run

```bash
curl -X POST "http://localhost:8000/agents/runs/<run_id>/cancel"
```

### Work Mode: resume failed/canceled run

```bash
curl -X POST "http://localhost:8000/agents/runs/<run_id>/resume"
```

## Memory 2.0 Foundation (Current)

Implemented now:
- four memory layers in runtime context:
  - `working` (session-scoped short-term memory)
  - `episodic` (interaction timeline)
  - `semantic` (retrieval memory)
  - `profile` (user preferences/profile)
- typed memory context models (`memory/models.py`)
- extraction rules moved to dedicated service (`memory/extraction_service.py`)
- extraction records and conflict audit tables in SQLite
- conflict policy (`latest/high-confidence wins`) for profile and semantic facts
- semantic retrieval scoring (vector + recency + confidence + importance)
- stronger consolidation:
  - same-value semantic redundancy collapse (`consolidated_redundant_value`)
  - cross-value winner selection with rank-aware dedup (`consolidated_duplicate`)
- profile confidence decay projection (source-aware, age-aware) in context/debug
- profile decay-aware preference overwrite policy (stale profile entries can be replaced safely)
- memory quality eval suites (`core`, `extended`) for regression checks
- local telemetry events for memory (`memory_extract`, `memory_conflict`, `memory_retrieval`, `memory_retrieval_debug`)
- backward-compatible memory manager API for existing agent loop

SQLite tables added in migration `v2`:
- `working_memory`
- `memory_extractions`
- `memory_conflicts`

## Provider-Agnostic Core 2.0 Foundation (Current)

Implemented now:
- unified provider error taxonomy (`rate_limit`, `quota`, `timeout`, `auth`, `invalid_request`, `server`, `network`, `circuit_open`, `budget_limit`, `unavailable`, `unknown`)
- policy-driven failover orchestration in `ModelManager` for both normal and streaming chat
- budget-aware routing score penalty under cloud guardrail pressure
- session-level route pinning via `session_id` (stable provider/model continuity per chat session)
- failover diagnostics embedded into chat `routing` payload (`final`, `failover_events`)
- failover state debug API:
  - `GET /debug/models/failover?session_id=<id>&limit=100`

## Agents Work Mode Foundation (Current)

Implemented now:
- async run queue for agents (`queued` -> `running` -> `succeeded|failed|canceled`)
- persistent run state in SQLite (`agent_runs`)
- persistent issue-level state in SQLite (`agent_run_issues`)
- persistent issue artifacts in SQLite (`agent_run_issue_artifacts`)
- run lease ownership with CAS semantics (`lease_owner`, `lease_token`, `lease_expires_at`)
- lease release guarded by owner+token (prevents cross-worker lease clobbering)
- deterministic run outcomes: `failure_class` + terminal `stop_reason`
- failure-class retry policy (retry only for transient classes)
- run-level execution budgets:
  - `max_tokens`
  - `max_duration_sec`
  - `max_tool_calls`
  - `max_tool_errors`
- run checkpoints (stage history) including task-level phases:
  - `strategy_selected`, `plan_created`, `memory_loaded`
  - `reasoning_started`, `llm_response`, `tool_call_*`, `llm_followup_response`
  - `verification_*` (response verifier + repair loop)
  - `reasoning_completed`, `memory_updated`
- issue-based state machine per run:
  - statuses: `planned|running|blocked|done|failed`
  - core issues: `prepare_context`, `reasoning`, `persist`
  - planner issues: `plan_step:<n>` with dependency chain
- typed planner-step contracts:
  - step kinds resolved through `tasks/step_registry.py`
  - contract tokens: `preconditions`, `postconditions`, `max_retries`, `replan_allowed`
- step-level verifier for postconditions with failure scorecard in artifact payload
- planner issue retry + replan policy (checkpointed via `plan_step_retry_scheduled` and `plan_step_replanned`)
- bounded parallel execution for independent planner issues (dependency-aware worker pool)
- issue-level deadline guardrail with timeout failure propagation to run state
- final reasoning context now includes normalized issue artifacts from completed planner issues
- artifact quality gate for plan-step artifacts with:
  - deterministic merge policy (`latest_issue_wins` on field conflicts)
  - quality scorecard (`overall_score`, component scores, per-issue scoring, repair priority)
  - repair loop for problematic artifacts (bounded by config)
  - quality checkpoints: `artifact_quality_evaluated|artifact_repair_attempt|artifact_quality_passed|artifact_quality_failed`
- run resume restores issue/checkpoint state and continues from unfinished issues
- run resume hydrates `issue_artifacts` from persisted storage even when checkpoints are missing
- deterministic tool-call argument contract validation before tool execution
- exactly-once tool-call reliability for run retries/resume:
  - persisted idempotency log in SQLite (`agent_run_tool_calls`)
  - cached reuse of previously succeeded tool results by `idempotency_key`
  - crash-safe checkpoint bundle write (`checkpoint + issue state + issue artifact + tool call record`)
- startup crash recovery for unfinished runs:
  - `running` runs are moved back to `queued` with `recovered_after_crash` checkpoint
  - queued/running runs are re-enqueued on runtime start
- automatic retry until `max_attempts`
- manual cancel and resume APIs
- emergency run kill-switch API (`POST /service/runs/kill-switch`)
- checkpoint replay API (`GET /agents/runs/{run_id}/replay`) with timeline + attempt summary
- run diagnostics API (`GET /agents/runs/{run_id}/diagnostics`) with compact warnings and remediation hints
- run diagnostics package API (`GET /agents/runs/{run_id}/diagnostics/package`) with replay and evidence bundle
- run issues API (`GET /agents/runs/{run_id}/issues`)
- run artifacts API (`GET /agents/runs/{run_id}/artifacts`)
- run health/SLO debug API (`GET /debug/agents/runs/health`)
- status validation for run filters in API (`queued|running|succeeded|failed|canceled`)
- desktop Agents tab run monitor:
  - queue run from message input
  - live SSE event stream until terminal state (polling fallback on stream interruption)
  - watch mode indicator (`LIVE` / `FALLBACK` / `TERMINAL`) with last event timestamp
  - cancel/resume actions
  - checkpoint timeline and result preview
  - mission diagnostics pane with warning chips, signal badges, recommended actions, and quick replay presets
  - replay loader with attempt summary and event timeline
  - replay timeline filters with presets (`errors`, `tools`, `verify`), pagination, side-by-side attempt diff, and diagnostic package export

Run status values:
- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

## Security Baseline (Current)

Implemented now:
- authN/authZ middleware on all non-public routes
- fail-fast production config guard (runtime startup is rejected if:
  - `AMARYLLIS_AUTH_ENABLED=false`
  - auth tokens are empty
  - `AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT!=strict`
  - `AMARYLLIS_TOOL_SANDBOX_ENABLED!=true`
  - `AMARYLLIS_PLUGIN_SIGNING_MODE!=strict`
  - `AMARYLLIS_PLUGIN_RUNTIME_MODE!=sandboxed`)
- request scope enforcement:
  - `/security/*` and `/debug/*` -> `admin`
  - `/service/*` -> `service` or `admin`
  - business APIs -> `user` or `admin`
- structured deny auditing for `401` and `403` events
- signed security actions with local identity key
- identity rotation endpoint:
  - `POST /security/identity/rotate`
- security audit endpoints (admin scope):
  - `GET /security/identity`
  - `GET /security/audit`
- compliance operations endpoints (admin scope):
  - `GET /security/secrets`
  - `POST /security/secrets/sync`
  - `GET /security/auth/tokens/activity`
  - `POST /security/access-reviews/start`
  - `POST /security/access-reviews/{review_id}/complete`
  - `GET /security/access-reviews`
  - `GET /security/access-reviews/{review_id}`
  - `POST /security/incidents/open`
  - `POST /security/incidents/{incident_id}/ack`
  - `POST /security/incidents/{incident_id}/resolve`
  - `POST /security/incidents/{incident_id}/notes`
  - `GET /security/incidents`
  - `GET /security/incidents/{incident_id}`
  - `GET /security/compliance/snapshot`
  - `POST /security/compliance/evidence/export`
- security/compliance operational scripts:
  - `python scripts/security/compliance_check.py`
  - `python scripts/security/export_audit_evidence.py --window-days 90 --event-limit 2000`
- detailed runbook:
  - `docs/security-compliance-baseline.md`

## Tools + MCP Layer Foundation (Current)

Implemented now:
- tool isolation policy with explicit risk tiers and sandbox presets
- real subprocess sandbox for builtin/plugin tools with CPU/RAM/time limits, env sanitization, and strict JSON contract
- tool budget guardrails (window, per-tool, total, high-risk caps with session/user/request scoping)
- scoped + expiring permission prompts for risky tools (`pending -> approved/denied -> consumed|expired`)
- explicit high-risk action receipts for high/critical tool invokes (response `high_risk_action`, audit `event_type=high_risk_action_receipt`)
- batch permission handoff in chat API via `permission_ids`
- MCP server endpoints:
  - `GET /mcp/tools`
  - `POST /mcp/tools/{tool_name}/invoke`
- MCP client aggregation from remote MCP endpoints into local tool registry
- signed plugin manifest verification modes (`off|warn|strict`) with discovery report
- plugin capability isolation matrix with discovery/runtime policy gates (`filesystem_read|filesystem_write|network|process`)
- MCP endpoint health scoring with automatic temporary quarantine on repeated failures
- structured tool execution trace (`status`, `duration_ms`, `permission_prompt_id`) in chat responses
- telemetry events for tool controls:
  - `tool_budget_recorded`
  - `tool_budget_blocked`
  - `tool_policy_blocked`
  - `tool_permission_required`

## Automation Layer 2.0 Foundation (Current)

Implemented now:
- persistent automation schedules in SQLite (`automations`, `automation_events`)
- typed schedules (`interval`, `hourly`, `weekly`, `watch_fs`) with timezone-aware next-run calculation
- background scheduler loop (single-node) that queues agent runs
- lease-based scheduler claim/release for due jobs (single-dispatch safety across concurrent scheduler instances)
- dispatch dedup keys persisted in SQLite (`automation_dispatches`) to suppress duplicate queued runs for the same slot
- adaptive retry backoff + circuit-open cooldown on repeated automation queue failures
- manual `run now`, `pause`, `resume`, `delete`
- automation update endpoint for changing schedule/message/session without recreation
- automation event log for observability
- automation reliability/SLO debug snapshot API (`GET /debug/automations/health`)
- file watcher mode (`watch_fs`) that triggers runs only on detected file changes
- inbox/notification feed in SQLite (`inbox_items`) with read/unread state
- failure escalation policy (`none -> warning -> critical`) with auto-disable threshold
- desktop UI controls in Agents tab
  - create/edit watcher schedules (`watch_fs`) without CLI
  - view escalation/failure counters directly on automation cards
  - triage inbox notifications and mark read/unread
- mission planner endpoint for risk-aware preflight before schedule creation (`POST /automations/mission/plan`)
- mission template catalog endpoint for prebuilt planning profiles (`GET /automations/mission/templates`)
- mission policy catalog endpoint for per-mission reliability envelopes (`GET /automations/mission/policies`)

Automation API:

```bash
# plan mission (risk-aware cadence + dry-run + apply hint)
curl -X POST http://localhost:8000/automations/mission/plan \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent_id>",
    "user_id": "user-001",
    "message": "Run autonomous daily code health mission",
    "cadence_profile": "workday",
    "timezone": "UTC",
    "start_immediately": true
  }'

# list mission templates
curl http://localhost:8000/automations/mission/templates

# list mission policy profiles
curl http://localhost:8000/automations/mission/policies

# plan mission from template defaults (message/cadence inferred)
curl -X POST http://localhost:8000/automations/mission/plan \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent_id>",
    "user_id": "user-001",
    "template_id": "release_guard",
    "timezone": "UTC"
  }'

# create
curl -X POST http://localhost:8000/automations/create \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent_id>",
    "user_id": "user-001",
    "session_id": "session-001",
    "message": "Check latest project updates and summarize",
    "schedule_type": "weekly",
    "schedule": {
      "byday": ["MO", "WE", "FR"],
      "hour": 9,
      "minute": 30
    },
    "timezone": "Asia/Aqtau",
    "start_immediately": false
  }'

# update schedule/message/session
curl -X POST "http://localhost:8000/automations/<automation_id>/update" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Check release notes and summarize action items",
    "session_id": "session-001",
    "schedule_type": "hourly",
    "schedule": {
      "interval_hours": 4,
      "minute": 15
    },
    "timezone": "UTC"
  }'

# list
curl "http://localhost:8000/automations?user_id=user-001&agent_id=<agent_id>&limit=100"

# pause / resume / run now
curl -X POST "http://localhost:8000/automations/<automation_id>/pause"
curl -X POST "http://localhost:8000/automations/<automation_id>/resume"
curl -X POST "http://localhost:8000/automations/<automation_id>/run"

# events
curl "http://localhost:8000/automations/<automation_id>/events?limit=100"

# reliability + SLO snapshot
curl "http://localhost:8000/debug/automations/health?user_id=user-001&limit=500"

# watcher-based automation (folder polling)
curl -X POST http://localhost:8000/automations/create \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent_id>",
    "user_id": "user-001",
    "message": "Analyze file changes and summarize",
    "schedule_type": "watch_fs",
    "schedule": {
      "path": "/Users/yourname/Documents/inbox",
      "poll_sec": 10,
      "recursive": true,
      "glob": "*.md",
      "max_changed_files": 20
    },
    "timezone": "UTC",
    "start_immediately": true
  }'

# inbox notifications
curl "http://localhost:8000/inbox?user_id=user-001&unread_only=true&limit=100"
curl -X POST "http://localhost:8000/inbox/<item_id>/read"
curl -X POST "http://localhost:8000/inbox/<item_id>/unread"
```

Escalation env vars:
- `AMARYLLIS_AUTOMATION_ESCALATION_WARNING` (default `2`)
- `AMARYLLIS_AUTOMATION_ESCALATION_CRITICAL` (default `4`)
- `AMARYLLIS_AUTOMATION_ESCALATION_DISABLE` (default `6`)

Reliability env vars:
- `AMARYLLIS_AUTOMATION_LEASE_TTL_SEC` (default `30`)
- `AMARYLLIS_AUTOMATION_BACKOFF_BASE_SEC` (default `5`)
- `AMARYLLIS_AUTOMATION_BACKOFF_MAX_SEC` (default `300`)
- `AMARYLLIS_AUTOMATION_CIRCUIT_FAILURE_THRESHOLD` (default `4`)
- `AMARYLLIS_AUTOMATION_CIRCUIT_OPEN_SEC` (default `120`)

### Tooling API

List all tools with metadata:

```bash
curl "http://localhost:8000/tools"
```

List permission prompts:

```bash
curl "http://localhost:8000/tools/permissions/prompts?status=pending&limit=50"
```

Approve prompt:

```bash
curl -X POST "http://localhost:8000/tools/permissions/prompts/<prompt_id>/approve"
```

Deny prompt:

```bash
curl -X POST "http://localhost:8000/tools/permissions/prompts/<prompt_id>/deny"
```

Invoke MCP tool:

```bash
curl -X POST "http://localhost:8000/mcp/tools/<tool_name>/invoke" \
  -H "Content-Type: application/json" \
  -d '{"arguments":{},"session_id":"session-001"}'
```

Browser action adapter tool (provider-agnostic contract, default runtime is stub):

```bash
curl -X POST "http://localhost:8000/mcp/tools/browser_action/invoke" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"session-001",
    "arguments":{
      "action":"extract",
      "selector":"main",
      "timeout_ms":5000
    }
  }'
```

Linux desktop action adapter tool (Linux provider, non-Linux falls back to stub):

```bash
curl -X POST "http://localhost:8000/mcp/tools/desktop_action/invoke" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"session-001",
    "arguments":{
      "action":"clipboard_read"
    }
  }'
```

Note: high/critical tool success responses include `high_risk_action` (`actor`, `policy_level`, `rollback_hint`) and are persisted into `/security/audit` as `high_risk_action_receipt`.

List persisted action receipts (including `python_exec` and `desktop_action`):

```bash
curl "http://localhost:8000/tools/actions/terminal?tool_name=python_exec&session_id=session-001&limit=50"
```

Filesystem patch planner (preview -> approve -> apply):

```bash
# 1) preview patch (no file mutation)
curl -X POST "http://localhost:8000/tools/actions/filesystem/patches/preview" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/absolute/path/to/file.txt",
    "content": "new content",
    "user_id": "user-001",
    "session_id": "session-001"
  }'

# 2) approve preview
curl -X POST "http://localhost:8000/tools/actions/filesystem/patches/<preview_id>/approve"

# 3) apply approved patch (optionally pass permission_id in strict mode)
curl -X POST "http://localhost:8000/tools/actions/filesystem/patches/<preview_id>/apply" \
  -H "Content-Type: application/json" \
  -d '{"permission_id":"<prompt_id>"}'

# inspect previews
curl "http://localhost:8000/tools/actions/filesystem/patches?status=approved&session_id=session-001&limit=50"
```

Debug tool guardrails snapshot:

```bash
curl "http://localhost:8000/debug/tools/guardrails?session_id=session-001&scopes_limit=20&top_tools_limit=5"
```

Debug MCP endpoint health and quarantine state:

```bash
curl "http://localhost:8000/debug/tools/mcp-health"
```

## Memory Debug API

Desktop app now includes a structured Memory Debug inspector in `Settings`:
- layer view: `working / episodic / semantic / profile`
- retrieval scoring preview (`score`, `vector_score`, `recency_score`)
- extraction timeline and conflict log
- optional raw JSON view for each debug call

Get computed memory context for a user/session:

```bash
curl "http://localhost:8000/debug/memory/context?user_id=user-001&agent_id=<agent_id>&session_id=session-001&query=planning"
```

Get semantic retrieval trace with scoring components:

```bash
curl "http://localhost:8000/debug/memory/retrieval?user_id=user-001&query=my%20preferences&top_k=8"
```

Get extraction audit log:

```bash
curl "http://localhost:8000/debug/memory/extractions?user_id=user-001&limit=20"
```

Get conflict audit log:

```bash
curl "http://localhost:8000/debug/memory/conflicts?user_id=user-001&limit=20"
```

Run consolidation manually:

```bash
curl -X POST "http://localhost:8000/debug/memory/consolidate" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"user-001","session_id":"session-001","semantic_limit":1000}'
```

Inspect profile confidence decay projection:

```bash
curl "http://localhost:8000/debug/memory/profile-decay?user_id=user-001&limit=100"
```

Run memory quality eval suite:

```bash
curl -X POST "http://localhost:8000/debug/memory/eval" \
  -H "Content-Type: application/json" \
  -d '{"suite":"extended"}'
```

## Plugins

Plugins are auto-discovered from:
- `plugins/<plugin_name>/manifest.json`
- `plugins/<plugin_name>/tool.py`

Default runtime mode is sandboxed (`AMARYLLIS_PLUGIN_RUNTIME_MODE=sandboxed`).
In sandboxed mode, plugin manifest must include `tool` descriptor:

```json
{
  "name": "example_plugin",
  "version": "1.0.0",
  "compat": {
    "manifest_version": "v1",
    "tool_registry_api": "v1",
    "runtime_modes": ["sandboxed", "legacy"]
  },
  "capabilities": ["filesystem_read", "filesystem_write"],
  "tool": {
    "name": "example_tool",
    "description": "Example plugin tool",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": true},
    "risk_level": "medium",
    "approval_mode": "required",
    "entrypoint": "execute"
  }
}
```

Compatibility contract is enforced during discovery. If `compat` is missing or incompatible,
the plugin is blocked before loading with actionable reason in plugin discovery report
(`compat_incompatible:*`).

Capability policy is also enforced during discovery and execution. Unsupported or blocked
capabilities are rejected (`capability_incompatible:*`), and runtime policy gates can block
declared capabilities (for example `network`) unless explicitly allowed.

`tool.py` must expose:
- `execute(arguments, context)` (or `execute(arguments)` fallback)

Legacy in-process registration mode is available only for development via:
- `AMARYLLIS_PLUGIN_RUNTIME_MODE=legacy`

## Tests

Run unit tests (memory + work mode + tools/MCP + automation):

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

## Golden Task Eval (Phase 0 Foundation)

Golden task suite (developer workflows):
- `eval/golden_tasks/dev_v1.json`

Validate suite schema:

```bash
python3 scripts/eval/run_golden_tasks.py --validate-only
```

Run first 5 tasks against local runtime:

```bash
python3 scripts/eval/run_golden_tasks.py --max-tasks 5 --strict
```

Blocking performance smoke gate (chat/run/voice/stream critical paths + p95/error-rate budget):

```bash
python3 scripts/release/perf_smoke_gate.py --iterations 3 --max-p95-latency-ms 350 --max-error-rate-pct 0 --output artifacts/perf-smoke-report.json
```

Blocking fault-injection reliability gate (provider/network/tool fault classes + retry/recovery assertions):

```bash
python3 scripts/release/fault_injection_reliability_gate.py --retry-max-attempts 2 --scenario-timeout-sec 8 --min-pass-rate-pct 100 --output artifacts/fault-injection-reliability-report.json
```

Blocking mission queue concurrency/load gate (queue-drain + p95 queue wait/end-to-end + success-rate SLO):

```bash
python3 scripts/release/mission_queue_load_gate.py --runs-total 40 --submit-concurrency 8 --worker-count 4 --task-latency-ms 35 --scenario-timeout-sec 30 --min-success-rate-pct 99 --max-failed-runs 0 --max-p95-queue-wait-ms 1500 --max-p95-end-to-end-ms 5000 --output artifacts/mission-queue-load-report.json
```

Release quality dashboard snapshot (normalized benchmark artifact + trend delta):

```bash
python3 scripts/release/build_quality_dashboard_snapshot.py --perf-report artifacts/perf-smoke-report.json --fault-injection-report artifacts/fault-injection-reliability-report.json --mission-queue-report artifacts/mission-queue-load-report.json --runtime-lifecycle-report artifacts/runtime-lifecycle-smoke-report.json --baseline eval/baselines/quality/release_quality_dashboard_baseline.json --output artifacts/release-quality-dashboard.json --trend-output artifacts/release-quality-dashboard-trend.json
```

Mission success/recovery report pack (public KPI snapshot for release/nightly):

```bash
python3 scripts/release/build_mission_success_recovery_report.py --mission-queue-report artifacts/mission-queue-load-report.json --fault-injection-report artifacts/fault-injection-reliability-report.json --quality-dashboard-report artifacts/release-quality-dashboard.json --scope release --output artifacts/mission-success-recovery-report.json
```

Linux parity smoke gate (run/voice/tools/observability acceptance on Linux target):

```bash
python3 scripts/release/linux_parity_smoke.py --iterations 1 --output artifacts/linux-parity-smoke-report.json
```

Nightly extended reliability run (success/latency/stability + trend deltas):

```bash
python3 scripts/release/nightly_reliability_run.py --iterations 12 --baseline eval/baselines/reliability/nightly_smoke_baseline.json --strict
```

Blocking nightly SLO burn-rate gate (flags sustained error-budget burn anomalies):

```bash
python3 scripts/release/nightly_slo_burn_rate_gate.py --report artifacts/nightly-reliability-report.json --max-consecutive-request-breach-samples 2 --max-consecutive-run-breach-samples 2 --output artifacts/nightly-burn-rate-gate-report.json
```

Reference:
- `docs/nightly-reliability.md`
- `docs/nightly-slo-burn-rate-gate.md`
- `docs/fault-injection-reliability.md`
- `docs/mission-queue-load-gate.md`
- `docs/release-quality-dashboard.md`
- `docs/mission-success-recovery-report-pack.md`

Autonomy level contract (L0-L5):

```bash
export AMARYLLIS_AUTONOMY_LEVEL=l3
```

Autonomy policy-pack contract (schema validation gate):

```bash
export AMARYLLIS_AUTONOMY_POLICY_PACK_PATH=policies/autonomy/default.json
python3 scripts/release/check_autonomy_policy_pack.py
```

Reference:
- `docs/autonomy-levels.md`
- `docs/autonomy-policy-pack.md`

## Security CI Gate

GitHub Actions workflow:
- `.github/workflows/security-gate.yml`

Release/pull-request gate is blocking and includes:
- mandatory security suite (`auth/authz`, security config, signing/enforcement tests)
- policy gate (`scripts/security/policy_check.py`) that rejects insecure production config
- compliance baseline gate (`scripts/security/compliance_check.py`)
- evidence export smoke check (`scripts/security/export_audit_evidence.py`)
- SAST (`bandit`) at high severity/high confidence
- dependency vulnerability audit (`pip-audit`)
- SBOM generation (`CycloneDX`)

Release-gate workflow additionally generates:
- signed release provenance (`artifacts/release-provenance.json` + `artifacts/release-provenance.sig`)
- release dependency inventory SBOM (`artifacts/release-sbom.json`)
- source candidate archive with digest coverage (`artifacts/release-source.tar.gz`)

## Notes on MLX and Ollama

- MLX is the primary local inference provider.
- If fallback is enabled, runtime can automatically try local providers:
  - `mlx -> ollama` when MLX fails
  - `openai/anthropic/openrouter -> mlx/ollama` when cloud calls fail (for example `429` quota/rate-limit)
- You can optionally enable remote cloud providers: OpenAI, Anthropic and OpenRouter.
- Configure runtime via env:
  - `AMARYLLIS_COGNITION_BACKEND=model_manager|deterministic`
  - `AMARYLLIS_AUTH_ENABLED=true|false`
  - `AMARYLLIS_AUTH_TOKENS=token-user:user-001:user,token-admin:admin:admin|user,token-service:svc:service`
  - `AMARYLLIS_SECURITY_PROFILE=production|development`
  - `AMARYLLIS_AUTONOMY_LEVEL=l0|l1|l2|l3|l4|l5`
  - `AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES=false|true`
  - `AMARYLLIS_OLLAMA_FALLBACK=true|false`
  - `AMARYLLIS_OLLAMA_URL=http://localhost:11434`
  - `AMARYLLIS_TELEMETRY_PATH=~/Library/Application Support/amaryllis/data/telemetry.jsonl`
  - `AMARYLLIS_OPENAI_BASE_URL=https://api.openai.com/v1`
  - `AMARYLLIS_OPENAI_API_KEY=<your_key>`
  - `AMARYLLIS_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1`
  - `AMARYLLIS_ANTHROPIC_API_KEY=<your_key>`
  - `AMARYLLIS_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`
  - `AMARYLLIS_OPENROUTER_API_KEY=<your_key>`
  - `AMARYLLIS_RUN_WORKERS=2`
  - `AMARYLLIS_RUN_MAX_ATTEMPTS=2`
  - `AMARYLLIS_RUN_ATTEMPT_TIMEOUT_SEC=180`
  - `AMARYLLIS_RUN_LEASE_TTL_SEC=365` (must be >= `run_attempt_timeout_sec + 5`)
  - `AMARYLLIS_RUN_BUDGET_MAX_TOKENS=24000`
  - `AMARYLLIS_RUN_BUDGET_MAX_DURATION_SEC=300`
  - `AMARYLLIS_RUN_BUDGET_MAX_TOOL_CALLS=8`
  - `AMARYLLIS_RUN_BUDGET_MAX_TOOL_ERRORS=3`
  - `AMARYLLIS_TASK_ISSUE_PARALLEL_WORKERS=2`
  - `AMARYLLIS_TASK_ISSUE_TIMEOUT_SEC=15`
  - `AMARYLLIS_TASK_ARTIFACT_QUALITY_ENABLED=true`
  - `AMARYLLIS_TASK_ARTIFACT_QUALITY_MAX_REPAIR_ATTEMPTS=1`
  - `AMARYLLIS_TASK_STEP_VERIFIER_ENABLED=true`
  - `AMARYLLIS_TASK_STEP_MAX_RETRIES_DEFAULT=1`
  - `AMARYLLIS_TASK_STEP_REPLAN_MAX_ATTEMPTS=1`
  - `AMARYLLIS_AUTOMATION_POLL_SEC=2`
  - `AMARYLLIS_AUTOMATION_BATCH_SIZE=10`
  - `AMARYLLIS_MEMORY_PROFILE_DECAY_ENABLED=true`
  - `AMARYLLIS_MEMORY_PROFILE_DECAY_HALF_LIFE_DAYS=45`
  - `AMARYLLIS_MEMORY_PROFILE_DECAY_FLOOR=0.35`
  - `AMARYLLIS_MEMORY_PROFILE_DECAY_MIN_DELTA=0.05`
  - `AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT=strict|prompt_and_allow`
  - `AMARYLLIS_TOOL_SANDBOX_ENABLED=true|false`
  - `AMARYLLIS_TOOL_SANDBOX_TIMEOUT_SEC=12`
  - `AMARYLLIS_TOOL_SANDBOX_MAX_CPU_SEC=6`
  - `AMARYLLIS_TOOL_SANDBOX_MAX_MEMORY_MB=512`
  - `AMARYLLIS_TOOL_SANDBOX_ALLOW_NETWORK_TOOLS=web_search`
  - `AMARYLLIS_TOOL_SANDBOX_ALLOWED_ROOTS=/path/to/workspace,/path/to/data`
  - `AMARYLLIS_BLOCKED_TOOLS=python_exec,filesystem`
  - `AMARYLLIS_PLUGIN_SIGNING_KEY=<hmac_secret>`
  - `AMARYLLIS_PLUGIN_SIGNING_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00`
  - `AMARYLLIS_PLUGIN_SIGNING_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00`
  - `AMARYLLIS_PLUGIN_SIGNING_MODE=off|warn|strict`
  - `AMARYLLIS_PLUGIN_RUNTIME_MODE=sandboxed|legacy`
  - `AMARYLLIS_OPENAI_API_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00`
  - `AMARYLLIS_OPENAI_API_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00`
  - `AMARYLLIS_ANTHROPIC_API_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00`
  - `AMARYLLIS_ANTHROPIC_API_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00`
  - `AMARYLLIS_OPENROUTER_API_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00`
  - `AMARYLLIS_OPENROUTER_API_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00`
  - `AMARYLLIS_SECRET_ROTATION_MAX_AGE_DAYS=90`
  - `AMARYLLIS_SECRET_EXPIRY_WARNING_DAYS=14`
  - `AMARYLLIS_IDENTITY_ROTATION_MAX_AGE_DAYS=30`
  - `AMARYLLIS_ACCESS_REVIEW_MAX_AGE_DAYS=30`
  - `AMARYLLIS_EVIDENCE_DIR=~/Library/Application Support/amaryllis/evidence`
  - `AMARYLLIS_MCP_ENDPOINTS=http://localhost:9001,http://localhost:9002`
  - `AMARYLLIS_MCP_TIMEOUT_SEC=10`
  - `AMARYLLIS_MCP_FAILURE_THRESHOLD=2`
  - `AMARYLLIS_MCP_QUARANTINE_SEC=60`
  - `AMARYLLIS_OTEL_ENABLED=true|false`
  - `AMARYLLIS_OTEL_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces`
  - `AMARYLLIS_SLO_WINDOW_SEC=3600`
  - `AMARYLLIS_SLO_REQUEST_AVAILABILITY_TARGET=0.995`
  - `AMARYLLIS_SLO_REQUEST_LATENCY_P95_MS_TARGET=1200`
  - `AMARYLLIS_SLO_RUN_SUCCESS_TARGET=0.98`
  - `AMARYLLIS_SLO_MIN_REQUEST_SAMPLES=50`
  - `AMARYLLIS_SLO_MIN_RUN_SAMPLES=20`
  - `AMARYLLIS_SLO_INCIDENT_COOLDOWN_SEC=300`
  - `AMARYLLIS_BACKUP_ENABLED=true|false`
  - `AMARYLLIS_BACKUP_DIR=~/Library/Application Support/amaryllis/backups`
  - `AMARYLLIS_BACKUP_INTERVAL_SEC=3600`
  - `AMARYLLIS_BACKUP_RETENTION_COUNT=120`
  - `AMARYLLIS_BACKUP_RETENTION_DAYS=30`
  - `AMARYLLIS_BACKUP_VERIFY_ON_CREATE=true|false`
  - `AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED=true|false`
  - `AMARYLLIS_BACKUP_RESTORE_DRILL_INTERVAL_SEC=86400`
  - `AMARYLLIS_API_VERSION=v1`
  - `AMARYLLIS_RELEASE_CHANNEL=alpha|beta|stable`
  - `AMARYLLIS_API_DEPRECATION_SUNSET_DAYS=180`
  - `AMARYLLIS_API_COMPAT_CONTRACT_PATH=contracts/api_compat_v1.json`

## Example Environment Variables

```bash
export AMARYLLIS_HOST=localhost
export AMARYLLIS_PORT=8000
export AMARYLLIS_DEFAULT_PROVIDER=mlx
export AMARYLLIS_DEFAULT_MODEL=mlx-community/Qwen2.5-1.5B-Instruct-4bit
export AMARYLLIS_COGNITION_BACKEND=model_manager
export AMARYLLIS_AUTH_ENABLED=true
export AMARYLLIS_AUTH_TOKENS="token-user:user-001:user,token-admin:admin:admin|user,token-service:svc:service"
export AMARYLLIS_SECURITY_PROFILE=production
export AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES=false
export AMARYLLIS_OLLAMA_URL=http://localhost:11434
export AMARYLLIS_OLLAMA_FALLBACK=true
export AMARYLLIS_TELEMETRY_PATH=~/Library/Application\ Support/amaryllis/data/telemetry.jsonl
export AMARYLLIS_OPENAI_BASE_URL=https://api.openai.com/v1
export AMARYLLIS_OPENAI_API_KEY=replace_me
export AMARYLLIS_OPENAI_API_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00
export AMARYLLIS_OPENAI_API_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00
export AMARYLLIS_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
export AMARYLLIS_ANTHROPIC_API_KEY=replace_me
export AMARYLLIS_ANTHROPIC_API_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00
export AMARYLLIS_ANTHROPIC_API_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00
export AMARYLLIS_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
export AMARYLLIS_OPENROUTER_API_KEY=replace_me
export AMARYLLIS_OPENROUTER_API_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00
export AMARYLLIS_OPENROUTER_API_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00
export AMARYLLIS_RUN_WORKERS=2
export AMARYLLIS_RUN_MAX_ATTEMPTS=2
export AMARYLLIS_RUN_ATTEMPT_TIMEOUT_SEC=180
export AMARYLLIS_RUN_LEASE_TTL_SEC=365
export AMARYLLIS_RUN_BUDGET_MAX_TOKENS=24000
export AMARYLLIS_RUN_BUDGET_MAX_DURATION_SEC=300
export AMARYLLIS_RUN_BUDGET_MAX_TOOL_CALLS=8
export AMARYLLIS_RUN_BUDGET_MAX_TOOL_ERRORS=3
export AMARYLLIS_TASK_ISSUE_PARALLEL_WORKERS=2
export AMARYLLIS_TASK_ISSUE_TIMEOUT_SEC=15
export AMARYLLIS_TASK_ARTIFACT_QUALITY_ENABLED=true
export AMARYLLIS_TASK_ARTIFACT_QUALITY_MAX_REPAIR_ATTEMPTS=1
export AMARYLLIS_TASK_STEP_VERIFIER_ENABLED=true
export AMARYLLIS_TASK_STEP_MAX_RETRIES_DEFAULT=1
export AMARYLLIS_TASK_STEP_REPLAN_MAX_ATTEMPTS=1
export AMARYLLIS_AUTOMATION_POLL_SEC=2
export AMARYLLIS_AUTOMATION_BATCH_SIZE=10
export AMARYLLIS_MEMORY_PROFILE_DECAY_ENABLED=true
export AMARYLLIS_MEMORY_PROFILE_DECAY_HALF_LIFE_DAYS=45
export AMARYLLIS_MEMORY_PROFILE_DECAY_FLOOR=0.35
export AMARYLLIS_MEMORY_PROFILE_DECAY_MIN_DELTA=0.05
export AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT=strict
export AMARYLLIS_BLOCKED_TOOLS=
export AMARYLLIS_PLUGIN_SIGNING_KEY=
export AMARYLLIS_PLUGIN_SIGNING_KEY_ROTATED_AT=2026-03-14T00:00:00+00:00
export AMARYLLIS_PLUGIN_SIGNING_KEY_EXPIRES_AT=2026-06-30T00:00:00+00:00
export AMARYLLIS_PLUGIN_SIGNING_MODE=strict
export AMARYLLIS_MCP_ENDPOINTS=
export AMARYLLIS_MCP_TIMEOUT_SEC=10
export AMARYLLIS_MCP_FAILURE_THRESHOLD=2
export AMARYLLIS_MCP_QUARANTINE_SEC=60
export AMARYLLIS_SECRET_ROTATION_MAX_AGE_DAYS=90
export AMARYLLIS_SECRET_EXPIRY_WARNING_DAYS=14
export AMARYLLIS_IDENTITY_ROTATION_MAX_AGE_DAYS=30
export AMARYLLIS_ACCESS_REVIEW_MAX_AGE_DAYS=30
export AMARYLLIS_EVIDENCE_DIR=~/Library/Application\ Support/amaryllis/evidence
export AMARYLLIS_OTEL_ENABLED=true
export AMARYLLIS_OTEL_OTLP_ENDPOINT=
export AMARYLLIS_SLO_WINDOW_SEC=3600
export AMARYLLIS_SLO_REQUEST_AVAILABILITY_TARGET=0.995
export AMARYLLIS_SLO_REQUEST_LATENCY_P95_MS_TARGET=1200
export AMARYLLIS_SLO_RUN_SUCCESS_TARGET=0.98
export AMARYLLIS_BACKUP_ENABLED=true
export AMARYLLIS_BACKUP_DIR=~/Library/Application\ Support/amaryllis/backups
export AMARYLLIS_BACKUP_INTERVAL_SEC=3600
export AMARYLLIS_BACKUP_RETENTION_COUNT=120
export AMARYLLIS_BACKUP_RETENTION_DAYS=30
export AMARYLLIS_BACKUP_VERIFY_ON_CREATE=true
export AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED=true
export AMARYLLIS_BACKUP_RESTORE_DRILL_INTERVAL_SEC=86400
export AMARYLLIS_API_VERSION=v1
export AMARYLLIS_RELEASE_CHANNEL=stable
export AMARYLLIS_API_DEPRECATION_SUNSET_DAYS=180
export AMARYLLIS_API_COMPAT_CONTRACT_PATH=contracts/api_compat_v1.json
```

## Observability and SRE

- Dashboard template: `observability/grafana/dashboard-amaryllis.json`
- Alert rules: `observability/alerts/prometheus-rules.yml`
- Service endpoints:
  - `GET /service/observability/slo`
  - `GET /service/observability/incidents`
  - `GET /service/observability/metrics`
- Docs: `docs/observability-sre.md`

## Backup and Disaster Recovery

- Backup/DR docs: `docs/disaster-recovery.md`
- Service endpoints:
  - `GET /service/backup/status`
  - `GET /service/backup/backups`
  - `POST /service/backup/run`
  - `POST /service/backup/verify`
  - `POST /service/backup/restore-drill`
  - `POST /service/runs/kill-switch`
- CLI:

```bash
python scripts/disaster_recovery/backup_now.py --trigger manual-cli --verify true
python scripts/disaster_recovery/kill_switch_runs.py --reason emergency-stop --include-running true --include-queued true
python scripts/disaster_recovery/restore_drill.py
python scripts/disaster_recovery/restore_from_archive.py --archive /path/to/backup.tar.gz
```

## Security and Compliance Operations

- Compliance baseline docs: `docs/security-compliance-baseline.md`
- Admin security operations endpoints:
  - `GET /security/secrets`
  - `POST /security/secrets/sync`
  - `GET /security/auth/tokens/activity`
  - `POST /security/access-reviews/start`
  - `POST /security/access-reviews/{review_id}/complete`
  - `POST /security/incidents/open`
  - `POST /security/incidents/{incident_id}/ack`
  - `POST /security/incidents/{incident_id}/resolve`
  - `POST /security/compliance/evidence/export`
- CLI:

```bash
python scripts/security/compliance_check.py
python scripts/security/export_audit_evidence.py --window-days 90 --event-limit 2000
```

## API Lifecycle and Release Process

- Lifecycle policy docs: `docs/api-lifecycle.md`
- Compatibility contract: `contracts/api_compat_v1.json`
- Compatibility gate:

```bash
python scripts/release/api_compat_gate.py
```

- Canary smoke:

```bash
python scripts/release/canary_smoke.py
```

- Linux parity smoke:

```bash
python scripts/release/linux_parity_smoke.py --iterations 1 --output artifacts/linux-parity-smoke-report.json
```

- Disaster recovery gate:

```bash
python scripts/release/disaster_recovery_gate.py
```

- Compliance operations gate:

```bash
python scripts/release/compliance_ops_gate.py
```

- Runtime/SLO profile drift gate:

```bash
python scripts/release/check_runtime_profile_drift.py
```

- Rollback playbook: `docs/release-playbook.md`
- Linux parity matrix: `docs/linux-parity-matrix.md`
- Local rollback helper:

```bash
scripts/release/rollback_local.sh <tag-or-commit>
```

## Jarvis Roadmap (Local Cognitive Platform)

- Strategy and phased execution plan: `docs/jarvis-roadmap.md`
- Phase 0 implementation backlog (with DoD and sprint status): `docs/jarvis-phase0-backlog.md`
- Phase 1 implementation backlog (Developer Jarvis Alpha): `docs/jarvis-phase1-backlog.md`
- Phase 2 implementation backlog (Tier-1 Operator Beta): `docs/jarvis-phase2-backlog.md`
- Phase 3 implementation backlog (Jarvis 1.0 OSS): `docs/jarvis-phase3-backlog.md`
- Phase 4 implementation backlog (Jarvis Personal Operator): `docs/jarvis-phase4-backlog.md`
- ADR-0001 (kernel contract surface v1): `docs/adr-0001-cognitive-kernel-contracts.md`
- Cognition backend abstraction and runtime switching: `docs/cognition-backends.md`
- Runtime/SLO profiles and quality budgets: `docs/runtime-profiles.md`
- Eval/replay deterministic seeds and fixture snapshot policy: `docs/eval-replay-determinism.md`

## License

See `LICENSE`.
