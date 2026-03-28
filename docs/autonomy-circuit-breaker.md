# Autonomy Circuit Breaker

## Purpose

`autonomy circuit breaker` is a global emergency control that blocks new autonomous run creation while incident response is in progress.

This is different from kill switch:
- kill switch stops already queued/running runs,
- circuit breaker blocks future execute-mode runs until it is disarmed.

## Service Endpoints

Requires `service` or `admin` scope.

- `GET /service/runs/autonomy-circuit-breaker`
  - returns current breaker state (`armed`, revision, actor, reason, timestamps).
- `POST /service/runs/autonomy-circuit-breaker`
  - `action: arm|disarm`
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

## Operational Flow

1. Service operator arms breaker (`action=arm`) with incident reason.
2. Optional automatic kill switch interrupts active runs.
3. Investigate and fix issue.
4. Service operator disarms breaker (`action=disarm`).
5. Execute-mode run creation resumes.
