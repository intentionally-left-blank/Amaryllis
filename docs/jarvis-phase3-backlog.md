# Jarvis Phase 3 Backlog

## Objective
Open, extensible local cognitive platform with bounded autonomous missions (L4), runtime lifecycle productization, and public-quality benchmark signals.

## Status Legend
- `todo`
- `in_progress`
- `done`
- `blocked`

## Tier-1 Exit Criteria (Phase 3)
- Mission planning and scheduling supports risk-aware autonomous loops with explicit trust gates.
- Runtime lifecycle on Linux/macOS has deterministic service-management path (install/start/stop/rollback) with smoke checks.
- Skills/plugins operate under stable compatibility contracts with upgrade-safe validation.
- Public benchmark and reliability dashboards expose trendable quality signals.

## Epics and Tasks

### Epic A - Autonomous Mission Planner (L4)

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P3-A01 | done | Add risk-aware mission planning API before automation creation | `/automations/mission/plan` + planner module + tests | User can dry-run mission, get cadence-normalized plan and apply payload with trust recommendations |
| P3-A02 | todo | Add mission template catalog (code health/security/release/watchdog) | template registry + docs | User can create mission from template with minimal manual tuning |
| P3-A03 | todo | Add mission SLO policy overlays per automation | policy schema + scheduler enforcement | Mission profiles enforce per-mission reliability/risk envelopes |

### Epic B - Local Runtime Lifecycle Productization

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P3-B01 | in_progress | Define Linux/macOS runtime lifecycle service contract | lifecycle spec + manifests contract tests | Runtime service management path is deterministic and testable |
| P3-B02 | todo | Add lifecycle installer/uninstaller commands with rollback-safe behavior | lifecycle CLI + docs | Operator can install/start/stop/rollback runtime service without manual edits |
| P3-B03 | todo | Add lifecycle smoke and SLO startup gate in release pipeline | blocking CI gate + artifacts | Release fails on lifecycle/startup regressions |

### Epic C - Skills and Plugin Compatibility

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P3-C01 | todo | Version plugin/skill compatibility contract | schema + validator | Incompatible plugin manifests fail fast with actionable errors |
| P3-C02 | todo | Add plugin capability isolation matrix and policy checks | capability policy map + tests | Plugin actions are bounded by declared capabilities and policy gates |

### Epic D - Public Quality Signals

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P3-D01 | todo | Publish benchmark harness outputs as dashboard artifacts | dashboard pipeline + JSON snapshots | Quality trends are visible per release with historical comparability |
| P3-D02 | todo | Add mission success/recovery public report pack | report generator + docs | Reliability KPIs are automatically exported for each release/nightly |

## Current Sprint (P3-S0)

| ID | Status | Scope |
|---|---|---|
| P3-A01 | done | mission planner API (`POST /automations/mission/plan`) + cadence heuristics + risk-aware apply hint |
| P3-B01 | in_progress | runtime lifecycle contract + deterministic manifest renderer (`scripts/runtime/render_service_manifest.py`) with contract tests (`tests/test_runtime_service_manifest_renderer.py`) |
