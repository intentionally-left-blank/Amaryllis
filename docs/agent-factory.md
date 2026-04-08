# Agent Factory

## Goal

Create specialized agents from a single natural-language request:

- "создай агента для AI новостей каждый день в 08:15 из reddit и twitter"
- "create an agent for security news from openai.com and huggingface.co"

## API

- `GET /v1/agents/factory/contract`
- `GET /v1/agents/factory/source-policies`
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
   - `inference_reason` (raw explainability payload)
   - `inference_reason_view` (UI-ready explanation summary/highlights/confidence)
   - `automation` schedule (`schedule_type`, `schedule`, `timezone`, `start_immediately`)
3. Apply the same request via `/v1/agents/quickstart` (optionally with `idempotency_key`).
4. Retry safely with the same `idempotency_key` (no duplicate agent creation).
5. Read `first_result` snapshot in apply/chat responses:
   - `mode` (`scheduled`, `manual_only`, `automation_setup_failed`)
   - `next_run_at` + `next_run_eta_sec`
   - `run_health` status
   - `recovery_hints` actionable steps.

In macOS desktop (`Agents -> One-shot Quickstart`), `inference_reason_view` is rendered as:
- confidence/kind chips;
- highlight chips;
- conflict-resolution timeline;
- applied override chips.

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

## Source Policy Profiles

Use profile bundles to avoid manual JSON policy tuning:

- endpoint: `GET /v1/agents/factory/source-policies`
- catalog version: `source_policy_profiles_v1`
- built-in profiles:
  - `open_web_default`
  - `ai_news_channels`
  - `ai_research_allowlist`
  - `engineering_allowlist`

Apply profile through structured override:

```json
{
  "overrides": {
    "source_policy": {
      "profile": "ai_research_allowlist"
    }
  }
}
```

`profile` can be combined with explicit `mode/channels/domains` when deeper customization is required.

## Schedule/Timezone Inference

The planner supports multilingual schedule hints, including:

- grouped weekdays/weekends (`по будням`, `on weekends`);
- daypart hints (`утром`, `evening`, `noon`) when exact time is omitted;
- `am/pm` time format (`at 8:30pm`);
- dot time format (`at 7.15`);
- timezone aliases and abbreviations (`мск`, `PST`, `CET`, `UTC+5`);
- relative hourly phrasing (`in 3 hours`, `через 3 часа`) mapped to hourly schedule with `start_immediately=true`.
- additional multilingual hints (`entre semana`, `fin de semana`, `todo dia`, `her 4 saat`, `Tokyo`, `IST`, `KST`, `CDMX`).
- ambiguous timezone abbreviations (for example `IST`, `CST`) are still resolved deterministically, but `inference_reason_view.disambiguation_hints` is emitted for UI confirmation.

## Contract

Machine-readable contract:

- `contracts/agent_factory_v1.json`

Use this file for integration checks and for external clients that need stable response expectations.

## Quality Gate

Intent-inference regression gate:

- script: `scripts/release/agent_factory_intent_gate.py`
- fixture: `eval/fixtures/agent_factory/intent_inference_cases.json`
- docs: `docs/agent-factory-intent-gate.md`

Quickstart flow parity gate:

- script: `scripts/release/agent_factory_quickstart_flow_gate.py`
- fixture: `eval/fixtures/agent_factory/quickstart_flow_cases.json`
- docs: `docs/agent-factory-quickstart-flow-gate.md`

Plan performance gate:

- script: `scripts/release/agent_factory_plan_perf_gate.py`
- baseline envelope: `eval/baselines/quality/agent_factory_plan_perf_envelope.json`
- docs: `docs/agent-factory-plan-perf-gate.md`
