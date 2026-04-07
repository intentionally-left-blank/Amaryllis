# Amaryllis Phase 9 Execution Plan (6 Weeks)

## Objective

Operationalize Phase 9 as a product-scale program:
ship a reliable one-line agent creation experience, prove competitive product outcomes,
and lock in a measurable adoption flywheel.

Scope baseline:
- `docs/amaryllis-phase9-backlog.md`
- `docs/amaryllis-roadmap.md`

## Planning Horizon

- Window: 6 weeks
- Sprint cadence: 2 weeks
- Product wedge: autonomous daily intelligence agents (news/research/monitoring)
- Platform priority: macOS primary product UX, Linux parity in runtime contracts

## Work Packages

| WP | Backlog IDs | Priority | Goal | Primary Code Areas | Blocking Gate |
|---|---|---|---|---|---|
| WP-01 | P9-A01, P9-A02 | P0 | Make one-line quickstart flow deterministic and outcome-visible | `api/agent_api.py`, `api/chat_api.py`, `kernel/agent_factory.py`, `storage/` | quickstart flow contract gate |
| WP-02 | P9-B01, P9-B02 | P0 | Productize template catalog + safe source-policy bundles | `automation/mission_planner.py`, `contracts/`, `docs/`, `macos/` | template schema/replay gate |
| WP-03 | P9-C01, P9-C02, P9-C03 | P0 | Harden account/entitlement onboarding and diagnostics | `runtime/provider_sessions.py`, `runtime/entitlements.py`, `models/model_manager.py`, `api/` | entitlement diagnostics gate |
| WP-04 | P9-D01, P9-D02, P9-D03 | P1 | Add competitor-neutral benchmark harness and trend gate | `eval/`, `scripts/release/`, `.github/workflows/` | competitive benchmark gate |
| WP-05 | P9-E01, P9-E03 | P1 | Improve install-to-first-value and product progress artifacts | `runtime/server.py`, `api/`, `scripts/release/`, `docs/` | activation KPI gate |
| WP-06 | P9-E02 | P2 | Ecosystem starter lane for community templates/plugins | `docs/`, `examples/`, template validation scripts | ecosystem pack contract checks |

## Sprint Sequence

### Sprint P9-S0 (Weeks 1-2) - Product Wedge Reliability

Goal:
- reliably go from one sentence to a scheduled agent with visible first-value signals.

In-sprint scope:
- `WP-01` quickstart one-line contract polish (`plan/apply` + idempotency-safe replay).
- `WP-01` first-result snapshot (next run ETA, run health, recovery hints).
- `WP-02` initial template catalog shipping (`ai_news_daily` + 2 adjacent templates).
- `WP-03` provider session onboarding UX hardening.

Definition of done:
- create-agent flow works from chat and API paths with the same contract outcome;
- first-result metadata is present and validated in tests;
- template catalog works without custom payload authoring.

### Sprint P9-S1 (Weeks 3-4) - Safety + Benchmark Foundations

Goal:
- make product behavior safer by default and benchmark-ready.

In-sprint scope:
- `WP-02` source-policy bundles (`open_web/channels/allowlist` persona presets).
- `WP-02` template replay gate with deterministic snapshot checks.
- `WP-03` entitlement fallback policy (session <-> BYOK) with explicit error contracts.
- `WP-04` benchmark scenario dataset v1 for create/schedule/quality/recovery product flows.

Definition of done:
- source policy defaults are usable without manual domain lists;
- template regressions are blocking in CI;
- benchmark scenarios are deterministic and publish artifacts.

### Sprint P9-S2 (Weeks 5-6) - Competitive Signal + Growth Loop

Goal:
- tie release decisions to product competitiveness and activation outcomes.

In-sprint scope:
- `WP-03` entitlement diagnostics card/API for supportability.
- `WP-04` competitive benchmark gate + trend gate in release/nightly pipelines.
- `WP-05` install-to-first-agent wizard contract and KPI snapshot artifacts.
- `WP-05` release-facing product progress summary artifact (user-visible improvements).

Definition of done:
- release candidates fail on product KPI regressions, not only technical failures;
- benchmark trend and activation trend are emitted as machine-readable CI artifacts.

## KPI Matrix

| KPI | Baseline Source | Phase 9 Exit Target |
|---|---|---|
| One-phrase creation success rate | quickstart flow report | `>= 95%` |
| Time to first agent p95 | activation + quickstart telemetry | `<= 180 sec` |
| First scheduled run success rate | mission reports | `>= 98%` |
| Template apply success rate | template gate report | `>= 99%` |
| Install-to-first-value conversion | adoption KPI snapshot | `>= 60%` |
| D7 retention proxy | adoption KPI trend report | `>= 30%` |

## Gate Matrix (Phase 9 additions)

| Gate | Script | Type |
|---|---|---|
| quickstart one-line flow gate | `scripts/release/agent_factory_quickstart_flow_gate.py` | blocking |
| template replay/schema gate | `scripts/release/agent_template_contract_gate.py` | blocking |
| entitlement diagnostics gate | `scripts/release/provider_entitlement_diagnostics_gate.py` | blocking |
| competitive benchmark gate | `scripts/release/competitive_benchmark_gate.py` | blocking |
| competitive benchmark trend gate | `scripts/release/competitive_benchmark_trend_gate.py` | blocking |
| activation KPI gate | `scripts/release/activation_kpi_gate.py` | blocking |

## Start-Now PR Slices (First 10 Working Days)

| PR | Window | Scope | Suggested Files | Exit Check |
|---|---|---|---|---|
| PR-1 | Day 1-2 | quickstart contract parity (`chat` vs `api`) | `api/chat_api.py`, `api/agent_api.py`, `tests/` | parity tests green |
| PR-2 | Day 2-3 | first-result snapshot payload | `kernel/agent_factory.py`, `api/agent_api.py`, `contracts/` | payload contract tests green |
| PR-3 | Day 3-4 | template catalog v1 | `automation/mission_planner.py`, `docs/`, `tests/` | catalog + apply tests green |
| PR-4 | Day 4-5 | provider session onboarding UX | `runtime/provider_sessions.py`, `api/`, `docs/` | onboarding flow tests green |
| PR-5 | Day 6-7 | source-policy bundles | `kernel/agent_factory.py`, `contracts/`, `tests/` | policy preset tests green |
| PR-6 | Day 8-10 | template replay gate scaffolding | `eval/fixtures/agent_templates/`, `scripts/release/`, `.github/workflows/` | gate report artifact produced |

## Phase 9 Exit Review

Before marking `completed`:
- all P9 `P0` work packages are release-gated;
- two consecutive weekly runs show KPI targets in-range;
- roadmap and backlog status updated with evidence artifacts.
