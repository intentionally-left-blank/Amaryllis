# Jarvis Phase 3 Backlog

## Objective
Open, extensible local cognitive platform with bounded autonomous missions (L4), runtime lifecycle productization, and public-quality benchmark signals.

## Phase Status
`completed` (all Phase 3 backlog items are done; next execution track: Phase 4).

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
| P3-A02 | done | Add mission template catalog (code health/security/release/watchdog) | template registry + docs | User can create mission from template with minimal manual tuning |
| P3-A03 | done | Add mission SLO policy overlays per automation | policy schema + scheduler enforcement | Mission profiles enforce per-mission reliability/risk envelopes |

### Epic B - Local Runtime Lifecycle Productization

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P3-B01 | done | Define Linux/macOS runtime lifecycle service contract | lifecycle spec + manifests contract tests | Runtime service management path is deterministic and testable |
| P3-B02 | done | Add lifecycle installer/uninstaller commands with rollback-safe behavior | lifecycle CLI + docs | Operator can install/start/stop/rollback runtime service without manual edits |
| P3-B03 | done | Add lifecycle smoke and SLO startup gate in release pipeline | blocking CI gate + artifacts | Release fails on lifecycle/startup regressions |

### Epic C - Skills and Plugin Compatibility

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P3-C01 | done | Version plugin/skill compatibility contract | schema + validator | Incompatible plugin manifests fail fast with actionable errors |
| P3-C02 | done | Add plugin capability isolation matrix and policy checks | capability policy map + tests | Plugin actions are bounded by declared capabilities and policy gates |

### Epic D - Public Quality Signals

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P3-D01 | done | Publish benchmark harness outputs as dashboard artifacts | dashboard pipeline + JSON snapshots | Quality trends are visible per release with historical comparability |
| P3-D02 | done | Add mission success/recovery public report pack | report generator + docs | Reliability KPIs are automatically exported for each release/nightly |

## Current Sprint (P3-S0)

| ID | Status | Scope |
|---|---|---|
| P3-A01 | done | mission planner API (`POST /automations/mission/plan`) + cadence heuristics + risk-aware apply hint |
| P3-A02 | done | mission template catalog endpoint (`GET /automations/mission/templates`) + template-aware mission planning defaults |
| P3-A03 | done | mission policy overlays (`GET /automations/mission/policies`) + per-automation scheduler enforcement (`warning/critical/disable/backoff/circuit`) |
| P3-B01 | done | runtime lifecycle contract + deterministic manifest renderer (`scripts/runtime/render_service_manifest.py`) with contract tests (`tests/test_runtime_service_manifest_renderer.py`) |
| P3-B02 | done | lifecycle manager CLI (`scripts/runtime/manage_service.py`) with install/uninstall/start/stop/status/rollback paths, rollback-safe install behavior, and tests |
| P3-B03 | done | blocking lifecycle smoke + startup SLO release gate (`scripts/release/runtime_lifecycle_smoke_gate.py`) with JSON artifact wiring in CI |
| P3-C01 | done | versioned plugin compatibility contract (`tools/plugin_compat.py`) + registry fail-fast enforcement (`tools/tool_registry.py`) + contract tests |
| P3-C02 | done | plugin capability isolation matrix (`tools/plugin_capabilities.py`) + runtime policy/sandbox gates (`tools/policy.py`, `tools/sandbox_runner.py`) + tests |
| P3-D01 | done | release quality dashboard snapshot builder (`scripts/release/build_quality_dashboard_snapshot.py`) + baseline/trend artifacts wired in release CI |
| P3-D02 | done | mission success/recovery public KPI report pack (`scripts/release/build_mission_success_recovery_report.py`) exported in release/nightly workflows |
