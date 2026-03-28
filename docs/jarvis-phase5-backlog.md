# Jarvis Phase 5 Backlog

## Objective

Move from "feature-complete personal operator" to "incident-resilient autonomous runtime":
strict global autonomy controls, stronger service-operability contracts, and blocking reliability gates for emergency behavior.

## Status Legend

- `todo`
- `in_progress`
- `done`
- `blocked`

## Tier-1 Exit Criteria (Phase 5)

- Autonomous execution can be globally paused/resumed through service-controlled emergency controls.
- Incident response path is deterministic: arm breaker -> stop active runs -> verify status -> recover -> disarm.
- Release/nightly pipelines block regressions in emergency-control contracts.
- Operator-facing docs and runbooks expose the same behavior as runtime/API implementation.

## Epics and Tasks

### Epic A - Autonomy Emergency Controls

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P5-A01 | done | Add global autonomy circuit breaker for run creation | runtime state module + service API + run-manager enforcement | Execute-mode run creation is blocked while breaker is armed; plan-mode stays available |
| P5-A02 | done | Add scoped breaker policies (`global`, `user`, `agent`) | scope-aware breaker contract | Service operator can isolate blast radius without full global freeze |
| P5-A03 | done | Add breaker persistence/recovery policy | persisted breaker state + startup restore policy | Runtime restarts do not silently lose emergency state |

### Epic B - Incident Runbook and Operability

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P5-B01 | done | Add release/nightly blocking gate for breaker contract | `autonomy_circuit_breaker_gate.py` + CI wiring | Release/nightly fail on breaker contract regressions |
| P5-B02 | done | Add incident audit timeline for breaker transitions | signed audit events + query docs | Every arm/disarm is traceable by actor/reason/request id |
| P5-B03 | done | Add SLO-safe auto-recovery recommendations | diagnostics hints + playbook updates | Operators get deterministic recovery guidance after breaker incidents |
| P5-B04 | done | Add breaker stability soak drill cadence gate | multi-cycle breaker drill gate + mission pack integration | Release/nightly fail on breaker drill cadence or cycle-latency regressions |

## Current Sprint (P5-S0)

| ID | Status | Scope |
|---|---|---|
| P5-A01 | done | global autonomy circuit breaker state + `/service/runs/autonomy-circuit-breaker` API + run creation enforcement |
| P5-A02 | done | scoped autonomy breaker (`global/user/agent`) + targeted run-create blocking + scope-aware optional kill-switch |
| P5-A03 | done | persisted breaker state file + startup restore + fail-safe recovery policy |
| P5-B01 | done | breaker release/nightly gate wiring + docs/runbook alignment |
| P5-B02 | done | breaker transition timeline endpoint + signed audit event filtering + runbook docs |
| P5-B03 | done | response-level SLO-safe recovery guidance (`status/timeline/updates`) + runbook hints |
| P5-B04 | done | breaker stability soak gate (`global/user/agent` cycle drills) + KPI pack + nightly runtime export metrics |

## Next Checkpoint

- Phase 5 breaker objectives completed (`P5-A01..A03`, `P5-B01..B04`).
- Breaker incident path promoted to stability soak (nightly trend verification + failure drill cadence gate).
- Start Phase 6 cross-domain autonomy hardening scope.
