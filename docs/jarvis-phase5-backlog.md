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
| P5-A02 | todo | Add scoped breaker policies (`global`, `user`, `agent`) | scope-aware breaker contract | Service operator can isolate blast radius without full global freeze |
| P5-A03 | todo | Add breaker persistence/recovery policy | persisted breaker state + startup restore policy | Runtime restarts do not silently lose emergency state |

### Epic B - Incident Runbook and Operability

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P5-B01 | done | Add release/nightly blocking gate for breaker contract | `autonomy_circuit_breaker_gate.py` + CI wiring | Release/nightly fail on breaker contract regressions |
| P5-B02 | todo | Add incident audit timeline for breaker transitions | signed audit events + query docs | Every arm/disarm is traceable by actor/reason/request id |
| P5-B03 | todo | Add SLO-safe auto-recovery recommendations | diagnostics hints + playbook updates | Operators get deterministic recovery guidance after breaker incidents |

## Current Sprint (P5-S0)

| ID | Status | Scope |
|---|---|---|
| P5-A01 | done | global autonomy circuit breaker state + `/service/runs/autonomy-circuit-breaker` API + run creation enforcement |
| P5-B01 | done | breaker release/nightly gate wiring + docs/runbook alignment |

## Next Checkpoint

- Complete CI artifact publication for breaker gate in release and nightly workflows.
- Validate service scope and signed-action audit coverage in security suite.
- Move to `P5-A02` scoped breaker policy design after 3 stable nightlies.
