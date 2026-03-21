# Agent Run Interaction Modes

## Purpose

Explicitly separate planning from execution so users can choose trust boundary per request:

- plan first (`dry-run`)
- execute now (create and run mission)

## Endpoints

- `GET /agents/runs/interaction-modes`
- `POST /agents/{agent_id}/runs/dispatch`

Legacy endpoints remain available:

- `POST /agents/{agent_id}/runs`
- `POST /agents/{agent_id}/runs/simulate`

## Request Contract (`/runs/dispatch`)

Payload fields:

- `user_id` (required)
- `message` (required)
- `interaction_mode` (`plan` or `execute`, default `execute`)
- `session_id` (optional)
- `max_attempts` (optional)
- `budget` (optional, same schema as run create/simulate)

## Behavior

### `interaction_mode=plan`

- Runs simulation only.
- No async run is created.
- No tools are executed.
- Returns:
  - `simulation`
  - `dry_run_receipt`
  - `execute_hint` (ready payload for same endpoint with `interaction_mode=execute`)
  - `trust_boundary` with `execution_performed=false`

### `interaction_mode=execute`

- Creates async run immediately.
- Returns:
  - `run`
  - `action_receipt`
  - `trust_boundary` with `execution_performed=true`

## Trust Boundary Signal

Response always includes:

- `interaction_mode`
- `trust_boundary`
- `supported_interaction_modes`

UI can map this directly to "Plan" vs "Execute" controls without guessing server behavior.
