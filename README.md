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

- no remote telemetry; runtime writes local telemetry file only
- no personal paths or machine-specific identifiers in repository files
- local-first runtime, data stays on your machine unless tools/providers call external services

## MVP Scope

Implemented in this version:
- FastAPI backend runtime
- native macOS UI (`SwiftUI`) with dark amaryllis theme
- OpenAI-compatible endpoint: `POST /v1/chat/completions`
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
- streaming chat UI
- model load/download progress indicators
- persistent local chat history (multi-chat sessions) in macOS app
- Agents tab automation UI with `watch_fs` + inbox read/unread controls
- centralized structured API errors (`error.type`, `error.message`, `error.request_id`)
- provider diagnostics endpoint: `GET /health/providers`
- SQLite migration framework (`schema_migrations`)
- local structured telemetry (`telemetry.jsonl`)

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
│   ├── chat_api.py
│   ├── inbox_api.py
│   ├── memory_api.py
│   ├── model_api.py
│   └── tool_api.py
├── controller
│   └── meta_controller.py
├── memory
│   ├── extraction_service.py
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
│   ├── config.py
│   └── server.py
├── storage
│   ├── database.py
│   └── vector_store.py
├── tasks
│   └── task_executor.py
├── tests
│   ├── test_agent_run_manager.py
│   ├── test_automation_schedule.py
│   ├── test_automation_scheduler.py
│   ├── test_memory_manager.py
│   ├── test_model_routing.py
│   └── test_tools_mcp.py
├── tools
│   ├── builtin_tools
│   │   ├── filesystem.py
│   │   ├── python_exec.py
│   │   └── web_search.py
│   ├── mcp_client_registry.py
│   ├── permission_manager.py
│   ├── policy.py
│   ├── tool_executor.py
│   └── tool_registry.py
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

Manual backend setup:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## Run

```bash
uvicorn runtime.server:app --host localhost --port 8000 --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

Provider health:

```bash
curl http://localhost:8000/health/providers
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

In app settings:
- set `API Endpoint` (default `http://localhost:8000`)
- set `Runtime Directory` to your repository root
- set optional cloud provider URLs and API keys:
  - OpenAI (`https://api.openai.com/v1`)
  - OpenRouter (`https://openrouter.ai/api/v1`)
- use `Start Runtime` to run the Python backend from UI
- API keys entered in app settings are stored in macOS Keychain
- use `Memory Debug` section to inspect context/retrieval/extractions/conflicts directly from UI
- in `Agents` tab, configure interval/hourly/weekly/watcher automations and process inbox alerts
- desktop UI theme uses retro terminal styling (80s-inspired) with bundled `OlivettiThin 9x14` bitmap font

Font attribution:
- see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

Chat tab behavior:
- create multiple chats (`New Chat`)
- switch chats from the chat selector
- full chat history is saved automatically and restored after restart
- supports `Auto Route` policy modes (`balanced`, `local_first`, `quality_first`, `coding`, `reasoning`)

Local chat file:
- `~/Library/Application Support/amaryllis/chat_sessions.json`

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

### Download model (MLX)

```bash
curl -X POST http://localhost:8000/models/download \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "provider": "mlx"
  }'
```

Tip: `/models` now returns `suggested` model lists for both `mlx` and `ollama`, and UI shows quick download actions for them.

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
- non-stream responses include `tool_events` trace with status and duration
- when a tool requires approval, `tool_events` includes `permission_prompt_id`
- after approving prompt(s), resend with `permission_ids` to continue tool execution

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
- local telemetry events for memory (`memory_extract`, `memory_conflict`, `memory_retrieval`, `memory_retrieval_debug`)
- backward-compatible memory manager API for existing agent loop

SQLite tables added in migration `v2`:
- `working_memory`
- `memory_extractions`
- `memory_conflicts`

## Agents Work Mode Foundation (Current)

Implemented now:
- async run queue for agents (`queued` -> `running` -> `succeeded|failed|canceled`)
- persistent run state in SQLite (`agent_runs`)
- run checkpoints (stage history) including task-level phases:
  - `strategy_selected`, `plan_created`, `memory_loaded`
  - `reasoning_started`, `llm_response`, `tool_call_*`, `llm_followup_response`
  - `reasoning_completed`, `memory_updated`
- automatic retry until `max_attempts`
- manual cancel and resume APIs
- status validation for run filters in API (`queued|running|succeeded|failed|canceled`)
- desktop Agents tab run monitor:
  - queue run from message input
  - live polling until terminal state
  - cancel/resume actions
  - checkpoint timeline and result preview

Run status values:
- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

## Tools + MCP Layer Foundation (Current)

Implemented now:
- tool isolation policy (blocked tools + risk/approval metadata)
- permission prompts for risky tools (`pending -> approved/denied -> consumed`)
- batch permission handoff in chat API via `permission_ids`
- MCP server endpoints:
  - `GET /mcp/tools`
  - `POST /mcp/tools/{tool_name}/invoke`
- MCP client aggregation from remote MCP endpoints into local tool registry
- signed plugin manifest verification (HMAC-SHA256 when signing key is configured)
- structured tool execution trace (`status`, `duration_ms`, `permission_prompt_id`) in chat responses

## Automation Layer 2.0 Foundation (Current)

Implemented now:
- persistent automation schedules in SQLite (`automations`, `automation_events`)
- typed schedules (`interval`, `hourly`, `weekly`, `watch_fs`) with timezone-aware next-run calculation
- background scheduler loop (single-node) that queues agent runs
- manual `run now`, `pause`, `resume`, `delete`
- automation update endpoint for changing schedule/message/session without recreation
- automation event log for observability
- file watcher mode (`watch_fs`) that triggers runs only on detected file changes
- inbox/notification feed in SQLite (`inbox_items`) with read/unread state
- failure escalation policy (`none -> warning -> critical`) with auto-disable threshold
- desktop UI controls in Agents tab
  - create/edit watcher schedules (`watch_fs`) without CLI
  - view escalation/failure counters directly on automation cards
  - triage inbox notifications and mark read/unread

Automation API:

```bash
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

## Plugins

Plugins are auto-discovered from:
- `plugins/<plugin_name>/manifest.json`
- `plugins/<plugin_name>/tool.py`

`tool.py` must expose either:
- `register(registry, manifest)`
- or `register_tool(registry, manifest)`

## Tests

Run unit tests (memory + work mode + tools/MCP + automation):

```bash
~/Library/Application\ Support/amaryllis/runtime-src/.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```

## Notes on MLX and Ollama

- MLX is the primary local inference provider.
- If fallback is enabled, runtime can automatically try local providers:
  - `mlx -> ollama` when MLX fails
  - `openai/anthropic/openrouter -> mlx/ollama` when cloud calls fail (for example `429` quota/rate-limit)
- You can optionally enable remote cloud providers: OpenAI, Anthropic and OpenRouter.
- Configure fallback via env:
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
  - `AMARYLLIS_AUTOMATION_POLL_SEC=2`
  - `AMARYLLIS_AUTOMATION_BATCH_SIZE=10`
  - `AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT=prompt_and_allow|strict`
  - `AMARYLLIS_BLOCKED_TOOLS=python_exec,filesystem`
  - `AMARYLLIS_PLUGIN_SIGNING_KEY=<hmac_secret>`
  - `AMARYLLIS_MCP_ENDPOINTS=http://localhost:9001,http://localhost:9002`
  - `AMARYLLIS_MCP_TIMEOUT_SEC=10`

## Example Environment Variables

```bash
export AMARYLLIS_HOST=localhost
export AMARYLLIS_PORT=8000
export AMARYLLIS_DEFAULT_PROVIDER=mlx
export AMARYLLIS_DEFAULT_MODEL=mlx-community/Qwen2.5-1.5B-Instruct-4bit
export AMARYLLIS_OLLAMA_URL=http://localhost:11434
export AMARYLLIS_OLLAMA_FALLBACK=true
export AMARYLLIS_TELEMETRY_PATH=~/Library/Application\ Support/amaryllis/data/telemetry.jsonl
export AMARYLLIS_OPENAI_BASE_URL=https://api.openai.com/v1
export AMARYLLIS_OPENAI_API_KEY=replace_me
export AMARYLLIS_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
export AMARYLLIS_ANTHROPIC_API_KEY=replace_me
export AMARYLLIS_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
export AMARYLLIS_OPENROUTER_API_KEY=replace_me
export AMARYLLIS_RUN_WORKERS=2
export AMARYLLIS_RUN_MAX_ATTEMPTS=2
export AMARYLLIS_AUTOMATION_POLL_SEC=2
export AMARYLLIS_AUTOMATION_BATCH_SIZE=10
export AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT=prompt_and_allow
export AMARYLLIS_BLOCKED_TOOLS=
export AMARYLLIS_PLUGIN_SIGNING_KEY=
export AMARYLLIS_MCP_ENDPOINTS=
export AMARYLLIS_MCP_TIMEOUT_SEC=10
```

## License

See `LICENSE`.
