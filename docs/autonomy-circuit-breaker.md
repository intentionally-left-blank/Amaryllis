# Autonomy Circuit Breaker

## Purpose

`autonomy circuit breaker` is a service-controlled emergency control that blocks new autonomous run creation while incident response is in progress.
It supports `global`, `user`, and `agent` scopes.

This is different from kill switch:
- kill switch stops already queued/running runs,
- circuit breaker blocks future execute-mode runs until it is disarmed.

## Service Endpoints

Requires `service` or `admin` scope.

- `GET /service/runs/autonomy-circuit-breaker`
  - returns current breaker state (`armed`, revision, actor, reason, timestamps).
- `POST /service/runs/autonomy-circuit-breaker`
  - `action: arm|disarm`
  - `scope_type: global|user|agent` (default: `global`)
  - `scope_user_id` (required when `scope_type=user`)
  - `scope_agent_id` (required when `scope_type=agent`)
  - optional `reason`
  - optional `apply_kill_switch` (default `true`) to interrupt existing queued/running runs at arm time.
  - optional kill-switch scope controls: `include_running`, `include_queued`, `limit`.

Existing endpoint:
- `POST /service/runs/kill-switch`
  - now also returns current `circuit_breaker` snapshot.

## Runtime Behavior

When breaker is armed:
- `POST /agents/{agent_id}/runs` is rejected with `validation_error`.
- `POST /agents/{agent_id}/runs/dispatch` with `interaction_mode=execute` is rejected.
- `interaction_mode=plan` remains available (dry-run planning only).

Scope behavior:
- `global`: blocks execute-mode run creation for all users/agents.
- `user`: blocks execute-mode run creation only for a specific `user_id`.
- `agent`: blocks execute-mode run creation only for a specific `agent_id`.

State persistence:
- Breaker state is persisted to `AMARYLLIS_AUTONOMY_CIRCUIT_BREAKER_STATE_PATH`
  (default: `<AMARYLLIS_DATA_DIR>/autonomy-circuit-breaker-state.json`).
- Runtime restart restores active scopes from this file before serving requests.
- If state file recovery fails (corrupted/invalid payload), runtime enters fail-safe mode:
  global breaker is armed (`reason=state_recovery_failed`) until service operator disarms it.

## Operational Flow

1. Service operator arms breaker (`action=arm`) with incident reason.
   Choose `scope_type`:
   - `global` for full emergency freeze,
   - `user` for tenant-scoped containment,
   - `agent` for single-agent containment.
2. Optional automatic kill switch interrupts active runs.
3. Investigate and fix issue.
4. Service operator disarms breaker (`action=disarm`).
5. Execute-mode run creation resumes.

Restart policy:
1. If runtime restarts while breaker is armed, it stays armed after restart.
2. Service operator verifies state via `GET /service/runs/autonomy-circuit-breaker`.
3. After incident closure, disarm explicitly.
