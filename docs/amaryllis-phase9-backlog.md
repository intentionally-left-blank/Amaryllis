# Amaryllis Phase 9 Backlog - Product Wedge and Competitive Scale

## Objective

Convert Amaryllis from a technically mature platform into a category-defining product lane:
"ask in one phrase -> get a reliable autonomous agent -> receive useful daily outcomes".

## Phase Status

`in_progress`

## Status Legend

- `todo`
- `in_progress`
- `done`
- `blocked`

## Tier-1 Exit Criteria (Phase 9)

- One-line agent creation flow reaches production reliability (`plan -> apply -> first scheduled run`) with low-friction UX.
- Install-to-first-value path is measurable and optimized across macOS and Linux.
- Agent templates and source-policy controls are reusable and safe by default.
- Competitive benchmark pack is reproducible and published as CI artifacts.
- Adoption/retention metrics are promoted to release decision inputs alongside technical gates.

## Epics and Tasks

### Epic A - One-Line Product Flow

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P9-A01 | done | Promote "one phrase" flow as first-class runtime contract | `/v1/agents/quickstart` + chat path parity for apply lifecycle and quickstart flow gate | User can request, preview, create, and schedule an agent from a single prompt with deterministic idempotency |
| P9-A02 | done | Add first-result contract for newly created agents | `agent_created` payload + first-run ETA/health snapshot | Response always includes explicit next-run timing and recovery hints |
| P9-A03 | todo | Harden plan explainability for non-technical users | simplified `inference_reason_view` profile + locale-safe wording | Users can understand why an agent was created/configured without reading internal policy jargon |

### Epic B - Template and Source Policy Productization

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P9-B01 | done | Add template catalog for daily intelligence missions | versioned template registry (`news/research/monitoring`) | User can choose and customize templates in one step, then run/schedule |
| P9-B02 | done | Add source-policy profile bundles | reusable `open_web/channels/allowlist` presets + domain packs | Users can safely scope internet access by persona/use-case without manual JSON editing |
| P9-B03 | done | Add template quality gate | release/nightly template contract + replay snapshots | CI blocks regressions in template schema and expected output fields |

### Epic C - Account Access and Entitlement UX

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P9-C01 | done | Productize provider-session onboarding UX | provider session create/list/revoke guide + API UX improvements | User can connect provider access without token leaks and with clear entitlement feedback |
| P9-C02 | done | Add fallback route policy (session vs BYOK) | deterministic routing policy + explicit error contracts | Runtime picks safe fallback path and explains missing entitlements clearly |
| P9-C03 | done | Add entitlement diagnostics card | machine-readable diagnostics for model access failures | Support/debug flow resolves account and quota issues without digging through logs |

### Epic D - Competitive Benchmark and Scoreboard

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P9-D01 | done | Define competitor-neutral benchmark scenarios | benchmark dataset for "create/schedule/quality/recovery" flows | Scenarios are reproducible, auditable, and not tied to one vendor API |
| P9-D02 | todo | Implement benchmark runner + report pack | `scripts/release/competitive_benchmark_gate.py` + artifact schema | CI emits scorecards with latency, reliability, and user-flow completion metrics |
| P9-D03 | todo | Add benchmark trend gate | baseline-aware trend checks in release/nightly | Regressions in product-level outcomes block release candidates |

### Epic E - Distribution and Ecosystem Flywheel

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P9-E01 | todo | Ship install-to-first-agent wizard contract | runtime/app onboarding handshake + progress telemetry | User reaches first working autonomous agent in guided flow without manual troubleshooting |
| P9-E02 | todo | Add template/plugin ecosystem starter lane | docs/examples + validation tooling for community templates | External contributors can publish template packs with policy-safe defaults |
| P9-E03 | todo | Add growth artifacts for release notes | machine-generated "what improved" user-facing diff from KPI reports | Each release publishes concise product progress signals for users and contributors |

## Sprint Plan (6-Week Cut)

### Sprint P9-S0 (Weeks 1-2)

| ID | Status | Scope |
|---|---|---|
| P9-A01 | done | one-phrase quickstart contract polish + chat/apply parity |
| P9-A02 | done | first-result/next-run snapshot contract |
| P9-B01 | done | minimal template catalog (`ai_news_daily` + 2 adjacent templates) |
| P9-C01 | done | provider-session onboarding UX hardening |

Sprint goal:
- new user can ask "create this agent" and reliably get a scheduled, observable, recoverable first outcome.

### Sprint P9-S1 (Weeks 3-4)

| ID | Status | Scope |
|---|---|---|
| P9-B02 | done | source-policy profile bundles + defaults |
| P9-B03 | done | template contract/replay quality gate |
| P9-C02 | done | entitlement fallback route policy |
| P9-D01 | done | benchmark scenario set v1 |

Sprint goal:
- user flow becomes safer by default and benchmarkable end-to-end.

### Sprint P9-S2 (Weeks 5-6)

| ID | Status | Scope |
|---|---|---|
| P9-C03 | done | entitlement diagnostics card |
| P9-D02 | todo | competitive benchmark gate + artifact wiring |
| P9-D03 | todo | benchmark trend gate |
| P9-E01 | todo | install-to-first-agent wizard contract |
| P9-E03 | todo | release-facing product progress artifacts |

Sprint goal:
- release decisions include product-level competitiveness and activation outcomes, not only technical health.

## KPI Targets for Phase Exit

- `one_phrase_agent_creation_success_rate_pct >= 95`
- `time_to_first_agent_p95_sec <= 180`
- `first_scheduled_run_success_rate_pct >= 98`
- `template_apply_success_rate_pct >= 99`
- `install_to_first_value_conversion_rate_pct >= 60`
- `d7_retention_proxy_pct >= 30`

## Next Checkpoint

- Publish execution plan with work packages and gate matrix: `docs/amaryllis-phase9-execution-plan.md`.
- Continue with `P9-D02` as next high-priority block after `P9-D01`.
- Promote product KPI snapshot to release KPI pack once first trend baseline is collected.
