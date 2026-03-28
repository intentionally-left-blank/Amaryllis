# Agent Run Explainability Feed

## Purpose

`GET /agents/runs/{run_id}/explain` provides a plain-language explainability payload for mission execution.

It complements:
- timeline stream: `GET /agents/runs/{run_id}/events`
- audit timeline: `GET /agents/runs/{run_id}/audit`

Goal: each action event is visible with:
- `reason` (why it happened),
- `result` (what happened),
- `next_step` (what user/operator should do next).

## API Contract

### Explainability payload

`GET /agents/runs/{run_id}/explain`

Query params:
- `include_tool_calls` (`true|false`, default `true`)
- `include_security_actions` (`true|false`, default `true`)
- `limit` (`1..20000`, default `2000`)

Response:
- `explainability.feed_version` (`run_explainability_feed_v1`)
- `explainability.summary` with terminal stop/failure and `recommended_actions`
- `explainability.items[]` with per-event plain-language fields:
  - `reason`
  - `result`
  - `next_step`

### Timeline stream

`GET /agents/runs/{run_id}/events`

SSE events include run snapshot/checkpoint progress and terminal `done` event for real-time UI timeline rendering.

## Safety

- owner check is enforced (cross-tenant access returns `403`)
- explainability is derived from persisted run checkpoints + audit context (no hidden side-channel execution)
