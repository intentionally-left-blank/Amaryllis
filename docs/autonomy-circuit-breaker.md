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
  - includes `recovery_guidance` with deterministic next steps (`status`, `priority`, `recommendations`) based on SLO and recent breaker transitions.
- `GET /service/runs/autonomy-circuit-breaker/domains`
  - cross-domain diagnostics snapshot for breaker impact across:
    - `runs` (failed execute admission attempts blocked by breaker),
    - `automations` (scheduler/manual dispatch pauses),
    - `supervisor` (child-node dispatch pauses).
  - returns `domain_impact` with per-domain blocked counters and `last_blocked_at`.
  - supports window controls: `limit`, `supervisor_graph_limit`, `supervisor_timeline_limit`.
- `POST /service/runs/autonomy-circuit-breaker`
  - `action: arm|disarm`
  - `scope_type: global|user|agent` (default: `global`)
  - `scope_user_id` (required when `scope_type=user`)
  - `scope_agent_id` (required when `scope_type=agent`)
  - optional `reason`
  - optional `apply_kill_switch` (default `true`) to interrupt existing queued/running runs at arm time.
  - optional kill-switch scope controls: `include_running`, `include_queued`, `limit`.
- `GET /service/runs/autonomy-circuit-breaker/timeline`
  - incident timeline of breaker transitions (signed audit stream).
  - filters: `limit`, `status`, `actor`, `transition=arm|disarm`, `scope_type=global|user|agent`, `request_id`.
  - each item includes `actor`, `request_id`, `transition.reason`, `transition.scope_*`, and signature metadata.
  - includes `recovery_guidance` aligned with current breaker state and observability SLO context.

Existing endpoint:
- `POST /service/runs/kill-switch`
  - now also returns current `circuit_breaker` snapshot.

## Runtime Behavior

When breaker is armed:
- `POST /agents/{agent_id}/runs` is rejected with `validation_error`.
- `POST /agents/{agent_id}/runs/dispatch` with `interaction_mode=execute` is rejected.
- `interaction_mode=plan` remains available (dry-run planning only).
- automation manual/scheduled dispatch (`POST /automations/{automation_id}/run` or scheduler tick) is paused with
  `run_blocked_autonomy_circuit_breaker` event instead of failure escalation (`consecutive_failures` is not incremented).
- supervisor node dispatch (`POST /supervisor/graphs/{graph_id}/launch|tick`) is paused per node when blocked by scope;
  node stays `planned` with timeline event `node_run_blocked_autonomy_circuit_breaker` until breaker is disarmed.

Scope behavior:
- `global`: blocks execute-mode run creation for all users/agents.
- `user`: blocks execute-mode run creation only for a specific `user_id`.
- `agent`: blocks execute-mode run creation only for a specific `agent_id`.

Scope parity applies to all execute dispatch domains:
- direct runs (`/agents/*/runs`, execute dispatch),
- automation dispatch (`/automations/*/run`, scheduler),
- supervisor child-run dispatch (`/supervisor/graphs/*/launch|tick`).

Operator diagnostics:
- `GET /service/runs/autonomy-circuit-breaker/domains` is the unified control-plane view for blocker impact
  across `runs`, `automations`, and `supervisor` in one payload.

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

## Incident Timeline

Recommended incident trace flow:
1. Trigger `arm` or `disarm` via service endpoint.
2. Query timeline endpoint filtered by `request_id` or `transition`.
3. Verify event has expected `actor`, `reason`, `scope_type`, and signed action metadata.
4. Read `recovery_guidance.recommendations` to execute safe unfreeze/recovery sequence.

This gives deterministic incident traceability for every breaker transition.

## Stability Soak Gate

Release/nightly reliability chains run multi-cycle breaker drills:

```bash
python3 scripts/release/autonomy_circuit_breaker_soak_gate.py \
  --cycles 6 \
  --min-success-rate-pct 100 \
  --max-failed-cycles 0 \
  --max-p95-cycle-latency-ms 4500 \
  --output artifacts/autonomy-circuit-breaker-soak-gate-report.json
```

Gate validates:
- deterministic `arm -> block -> timeline trace -> disarm -> execute restored` loop,
- scope behavior parity across `global`, `user`, and `agent`,
- cross-domain pause/resume parity for direct runs, automations, and supervisor child-run dispatch,
- p95 cycle latency and failed-cycle budget.
