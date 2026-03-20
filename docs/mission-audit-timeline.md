# Mission Audit Timeline

## Purpose

`GET /agents/runs/{run_id}/audit` gives a single audit timeline for an autonomous run.

The timeline combines:

- run checkpoints (`channel=run_checkpoint`)
- run tool calls (`channel=tool_call`)
- security audit actions (`channel=security_audit`)

Each event keeps actor/policy context so operators can answer:

- who triggered the action,
- which policy context was active,
- what happened before terminal stop/failure.

## Simple User Flow

1. User starts a mission run.
2. Run reaches terminal state (`succeeded`, `failed`, or `canceled`).
3. Client calls `GET /agents/runs/{run_id}/audit`.
4. User reviews:
   - full action timeline,
   - channel/status counters,
   - terminal `stop_reason` and `failure_class`.
5. If evidence is needed, user exports:
   - `GET /agents/runs/{run_id}/audit/export?format=json`
   - `GET /agents/runs/{run_id}/audit/export?format=csv`

## API Contract

### Read timeline

`GET /agents/runs/{run_id}/audit`

Query params:

- `include_tool_calls` (`true|false`, default `true`)
- `include_security_actions` (`true|false`, default `true`)
- `limit` (`1..20000`, default `2000`)

Response:

- `audit.timeline[]` with normalized event fields
- `audit.summary.channel_counts`
- `audit.summary.status_counts`
- `audit.summary.terminal_stop_reason`
- `audit.summary.terminal_failure_class`

### Export JSON

`GET /agents/runs/{run_id}/audit/export?format=json`

Returns JSON payload compatible with the same `audit` contract.

### Export CSV

`GET /agents/runs/{run_id}/audit/export?format=csv`

Returns CSV attachment (`Content-Disposition`) with flat columns:

- `timestamp`
- `channel`
- `event_id`
- `stage`
- `action`
- `status`
- `attempt`
- `actor`
- `target_type`
- `target_id`
- `message`
- `stop_reason`
- `failure_class`
- `request_id`

## Safety

- owner check is enforced (cross-tenant access returns `403`)
- invalid `format` returns `400 validation_error`

