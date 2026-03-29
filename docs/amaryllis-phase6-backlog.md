# Amaryllis Phase 6 Backlog

## Objective

Harden autonomy controls across execution domains so emergency state is consistent for direct runs, automations, and supervisor-driven flows.

## Phase Status

`completed` (all Phase 6 backlog items are done; next execution track: Phase 7 planning + parallel Post-LLM R&D lane).

## Status Legend

- `todo`
- `in_progress`
- `done`
- `blocked`

## Tier-1 Exit Criteria (Phase 6)

- Autonomy emergency controls are domain-consistent (`runs`, `automations`, `supervisor`).
- Breaker-armed maintenance mode does not create false reliability/escalation noise.
- Cross-domain autonomy hardening regressions are release/nightly blocking.

## Epics and Tasks

### Epic A - Cross-Domain Dispatch Consistency

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P6-A01 | done | Make automation dispatch breaker-aware without failure escalation | scheduler breaker-pause path + API behavior + gate checks | Breaker-armed automation dispatch is paused (`run_blocked_autonomy_circuit_breaker`) and does not increment `consecutive_failures` |
| P6-A02 | done | Add supervisor admission parity with breaker scopes | supervisor dispatch preflight contract | Supervisor mission dispatch respects global/user/agent breaker scope |
| P6-A03 | done | Add tool-action autonomy boundary policy | explicit action classes + breaker interaction policy | High-risk autonomous tool actions cannot bypass breaker domain constraints |

### Epic B - Visibility and Blocking Gates

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P6-B01 | done | Add cross-domain autonomy status surface | service diagnostics endpoint + docs | Operators see domain-level breaker impact (`runs/automations/supervisor`) in one contract |
| P6-B02 | done | Promote cross-domain hardening to blocking gate | release/nightly gate checks + report artifact | Pipelines fail on cross-domain autonomy regression scenarios |

## Current Sprint (P6-S0)

| ID | Status | Scope |
|---|---|---|
| P6-A01 | done | breaker-aware automation dispatch pause semantics + reliability-noise suppression + gate coverage |
| P6-A02 | done | supervisor admission parity (global/user/agent) |
| P6-A03 | done | tool-action autonomy boundary policy (`action_class` contract + breaker high-risk autonomous boundary enforcement) |
| P6-B01 | done | unified cross-domain autonomy diagnostics (`/service/runs/autonomy-circuit-breaker/domains`) |
| P6-B02 | done | mission report pack + release/nightly workflow wiring now require autonomy breaker gate KPI contract |

## Next Checkpoint

- Phase 6 closed: all cross-domain breaker hardening objectives (`P6-A01..A03`, `P6-B01..B02`) are complete and release/nightly-gated.
- Promote Phase 7 planning package with execution-ready backlog and first sprint cut.
