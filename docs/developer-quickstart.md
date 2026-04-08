# Developer Quickstart (OpenAI-Compatible Local API)

## Goal

Run Amaryllis locally and send the first OpenAI-compatible request in under 15 minutes.

## 1. Start Runtime

```bash
export AMARYLLIS_SUPPORT_DIR="$HOME/.amaryllis-support"
export AMARYLLIS_AUTH_ENABLED=true
export AMARYLLIS_AUTH_TOKENS='dev-token:user-001:user'
export AMARYLLIS_COGNITION_BACKEND=deterministic

python -m uvicorn runtime.server:app --host 127.0.0.1 --port 8000
```

`deterministic` backend is the fastest local path for integration checks.

## 2. Health Check

```bash
curl -s http://127.0.0.1:8000/health
```

## 3. First OpenAI-Compatible Request

Endpoint: `POST /v1/chat/completions`

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "system", "content": "You are concise."},
      {"role": "user", "content": "Say hello from local runtime"}
    ],
    "stream": false
  }'
```

## 4. SDK-Like Examples

- Python: `python examples/openai_compat/python_quickstart.py`
- Node.js: `node examples/openai_compat/node_quickstart.mjs`

Both examples call `POST /v1/chat/completions` and print the assistant reply.

## 5. Minimal SDK Wrappers

- Python helper: `sdk/python/amaryllis_openai_compat.py`
- JavaScript helper: `sdk/javascript/amaryllis_openai_compat.mjs`

Use these wrappers when you need quick integration without bringing a full external SDK.

## 6. One-Shot Agent Quickstart (Plan -> Apply)

Plan first (dry-run, no side effects):

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/quickstart/plan \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "request": "create a daily AI news agent from reddit and twitter at 08:15"
  }'
```

Then apply using `apply_hint.payload` from the plan response:

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/quickstart \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "request": "create a daily AI news agent from reddit and twitter at 08:15",
    "idempotency_key": "quickstart-<from-plan>"
  }'
```

Using the same `idempotency_key` with the same payload is replay-safe: retries return the same created agent instead of creating duplicates.

Apply/chat quickstart responses also include `first_result` (`quickstart_first_result_v1`) with:
- `mode`
- `next_run_at` and `next_run_eta_sec`
- `run_health`
- `recovery_hints`

Inspect factory contract:

```bash
curl -s http://127.0.0.1:8000/v1/agents/factory/contract \
  -H "Authorization: Bearer dev-token"
```

Inspect source-policy profile bundles:

```bash
curl -s http://127.0.0.1:8000/v1/agents/factory/source-policies \
  -H "Authorization: Bearer dev-token"
```

Custom web scope (domain allowlist) is inferred from your natural-language request:

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/quickstart/plan \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "request": "create a daily AI agent from openai.com and huggingface.co at 07:45"
  }'
```

`quickstart_plan.source_policy.mode=allowlist` and `quickstart_plan.source_policy.domains`
show the inferred internet scope before apply.

Optional structured override (advanced):

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/quickstart/plan \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "request": "create an agent for AI updates",
    "overrides": {
      "kind": "coding",
      "name": "Build Pilot",
      "focus": "python tooling",
      "source_policy": {
        "mode": "allowlist",
        "domains": ["pypi.org", "github.com"]
      },
      "automation": {
        "enabled": true,
        "schedule_type": "hourly",
        "schedule": {"interval_hours": 6, "minute": 10}
      }
    }
  }'
```

Profile-based source-policy override (no manual domain JSON):

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/quickstart/plan \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "request": "create an agent for AI research updates",
    "overrides": {
      "source_policy": {
        "profile": "ai_research_allowlist"
      }
    }
  }'
```

## 7. Optional: Reddit OAuth for News Ingest

By default Reddit source ingest uses public search JSON.  
For higher quota/stability, configure OAuth app credentials:

```bash
export AMARYLLIS_REDDIT_CLIENT_ID="<client-id>"
export AMARYLLIS_REDDIT_CLIENT_SECRET="<client-secret>"
export AMARYLLIS_REDDIT_REFRESH_TOKEN="<refresh-token-optional>"
export AMARYLLIS_REDDIT_USER_AGENT="amaryllis-news-agent/1.0"
export AMARYLLIS_X_BEARER_TOKEN="<x-bearer-token>"
```

When OAuth variables are set, the Reddit connector automatically switches to authenticated search and falls back to public mode only if OAuth call fails.  
X connector requires `AMARYLLIS_X_BEARER_TOKEN` for `recent search` ingestion.
