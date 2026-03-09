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

- no telemetry by default
- no personal paths or machine-specific identifiers in repository files
- local-first runtime, data stays on your machine unless tools/providers call external services

## MVP Scope

Implemented in this version:
- FastAPI backend runtime
- native macOS UI (`SwiftUI`) with dark amaryllis theme
- OpenAI-compatible endpoint: `POST /v1/chat/completions`
- model manager with MLX primary provider and Ollama fallback
- model APIs: list/download/load
- agent APIs: create/list/chat
- memory layer: episodic + semantic + user memory
- SQLite persistence
- vector search via FAISS (with local fallback behavior)
- tool registry/executor with builtin tools
- plugin discovery from `plugins/`
- sequential task loop: meta-controller -> planner -> reasoning -> tools -> response
- local runtime controls from the desktop app (start/stop/check)
- streaming chat UI
- model load/download progress indicators

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

## Project Structure

```text
.
├── agents
│   ├── agent.py
│   └── agent_manager.py
├── api
│   ├── agent_api.py
│   ├── chat_api.py
│   └── model_api.py
├── controller
│   └── meta_controller.py
├── memory
│   ├── episodic_memory.py
│   ├── memory_manager.py
│   ├── semantic_memory.py
│   └── user_memory.py
├── models
│   ├── model_manager.py
│   └── providers
│       ├── mlx_provider.py
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
├── tools
│   ├── builtin_tools
│   │   ├── filesystem.py
│   │   ├── python_exec.py
│   │   └── web_search.py
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
- use `Start Runtime` to run the Python backend from UI

## Model Management API

### List models

```bash
curl http://localhost:8000/models
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

Streaming mode:

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
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
    "user_id": "demo-user"
  }'
```

### List agents

```bash
curl "http://localhost:8000/agents?user_id=demo-user"
```

### Chat with agent

```bash
curl -X POST http://localhost:8000/agents/<agent_id>/chat \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "session_id": "demo-session",
    "message": "Find 3 sources about MLX and summarize them."
  }'
```

## Plugins

Plugins are auto-discovered from:
- `plugins/<plugin_name>/manifest.json`
- `plugins/<plugin_name>/tool.py`

`tool.py` must expose either:
- `register(registry, manifest)`
- or `register_tool(registry, manifest)`

## Notes on MLX and Ollama

- MLX is the primary local inference provider.
- If MLX fails and fallback is enabled, runtime can try Ollama.
- Configure fallback via env:
  - `AMARYLLIS_OLLAMA_FALLBACK=true|false`
  - `AMARYLLIS_OLLAMA_URL=http://localhost:11434`

## Example Environment Variables

```bash
export AMARYLLIS_HOST=localhost
export AMARYLLIS_PORT=8000
export AMARYLLIS_DEFAULT_PROVIDER=mlx
export AMARYLLIS_DEFAULT_MODEL=mlx-community/Qwen2.5-1.5B-Instruct-4bit
export AMARYLLIS_OLLAMA_URL=http://localhost:11434
export AMARYLLIS_OLLAMA_FALLBACK=true
```

## License

See `LICENSE`.
