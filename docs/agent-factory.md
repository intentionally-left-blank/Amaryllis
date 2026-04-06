# Agent Factory

## Goal

Create specialized agents from a single natural-language request:

- "создай агента для AI новостей каждый день в 08:15 из reddit и twitter"
- "create an agent for security news from openai.com and huggingface.co"

## API

- `GET /v1/agents/factory/contract`
- `POST /v1/agents/quickstart/plan`
- `POST /v1/agents/quickstart`
- `POST /v1/chat/completions` (intent shortcut)

## User Flow

1. Send intent text to `/v1/agents/quickstart/plan`.
2. Review plan fields:
   - `kind`, `name`, `focus`
   - `tools`
   - `sources`
   - `source_policy` (`mode`, `channels`, `domains`)
   - `inference_reason` (why factory picked this profile)
   - `automation` schedule (`schedule_type`, `schedule`, `timezone`, `start_immediately`)
3. Apply the same request via `/v1/agents/quickstart` (optionally with `idempotency_key`).
4. Retry safely with the same `idempotency_key` (no duplicate agent creation).

## Optional Overrides

For advanced control you can send structured overrides while keeping NL request as the primary entry:

- `overrides.kind`: `news | coding | general`
- `overrides.name`
- `overrides.focus`
- `overrides.tools`
- `overrides.source_policy`
- `overrides.automation`

## Source Policy Modes

- `open_web`: no explicit source constraints inferred.
- `channels`: channel constraints inferred (for example `reddit`, `twitter`, `web`).
- `allowlist`: explicit domain list inferred (for example `openai.com`, `huggingface.co`).

## Schedule/Timezone Inference

The planner supports multilingual schedule hints, including:

- grouped weekdays/weekends (`по будням`, `on weekends`);
- daypart hints (`утром`, `evening`, `noon`) when exact time is omitted;
- `am/pm` time format (`at 8:30pm`);
- timezone aliases and abbreviations (`мск`, `PST`, `CET`, `UTC+5`);
- relative hourly phrasing (`in 3 hours`, `через 3 часа`) mapped to hourly schedule with `start_immediately=true`.

## Contract

Machine-readable contract:

- `contracts/agent_factory_v1.json`

Use this file for integration checks and for external clients that need stable response expectations.

## Quality Gate

Intent-inference regression gate:

- script: `scripts/release/agent_factory_intent_gate.py`
- fixture: `eval/fixtures/agent_factory/intent_inference_cases.json`
- docs: `docs/agent-factory-intent-gate.md`

Plan performance gate:

- script: `scripts/release/agent_factory_plan_perf_gate.py`
- docs: `docs/agent-factory-plan-perf-gate.md`
