# Amaryllis Phase 7 Backlog

## Objective

Ship a production-grade autonomous news mission lane:
daily topic digests (for example AI), grounded by verifiable sources, with stable scheduling and provider entitlement controls.

## Phase Status

`in_progress` (implementation complete; release cut sign-off pending)

## Status Legend

- `todo`
- `in_progress`
- `done`
- `blocked`

## Tier-1 Exit Criteria (Phase 7)

- Daily news missions run on schedule and produce deterministic digest artifacts with source links.
- Source ingestion is connector-based (`reddit`, `x`, `web`) with retries, dedup, and per-source policy controls.
- Provider access supports secure user-scoped sessions plus explicit entitlement checks before remote model usage.
- Mission reliability regressions are release/nightly blocking via dedicated gate tests.
- Operator documentation and API contracts match runtime behavior.

## Product Constraints

- `ChatGPT Plus` is not treated as an OpenAI API key path for server-to-server calls.
- BYOK (`AMARYLLIS_OPENAI_API_KEY` and peers) remains supported and is the default cloud path.
- Provider session passthrough is an additive lane with explicit security boundaries, revocation, and audit.

## Epics and Tasks

### Epic A - Source Connectors (Reddit/X/Web)

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P7-A01 | done | Add unified source connector contract | `sources/base.py` + typed request/response schema | Runtime can call source connectors through a single contract with normalized item shape |
| P7-A02 | done | Implement Reddit connector (OAuth + search ingest) | `sources/reddit_connector.py` + policy/rate handling | Reddit items are ingested with canonical ids, timestamps, author, url, and retry policy |
| P7-A03 | done | Implement X connector (OAuth/Bearer + search ingest) | `sources/x_connector.py` + policy/rate handling | X items are ingested with the same normalized schema and source metadata |
| P7-A04 | done | Upgrade web connector from raw search snippets to fetch+extract path | `sources/web_connector.py` | Web items include URL, title, excerpt, publish hints, and fetch status |

### Epic B - Provider Session and Entitlement Layer

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P7-B01 | done | Add provider session store (user-scoped encrypted token refs) | `runtime/provider_sessions.py` + DB tables + APIs | Users can create/list/revoke provider sessions without exposing raw tokens in logs |
| P7-B02 | done | Add entitlement resolver contract | `runtime/entitlements.py` + `/auth/providers/*` APIs | Runtime can answer capability checks (`allowed_models`, `rate_tier`, `feature_flags`) per user/provider |
| P7-B03 | done | Wire model routing to entitlement gates | `models/model_manager.py` integration | Remote model calls fail fast with clear entitlement errors when access is missing |

### Epic C - News Mission Pipeline

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P7-C01 | done | Add news mission planner contract | `/news/missions/plan` API + `contracts/news_mission_v1.json` | Mission plan returns topic, source policy, schedule, budget, and output schema |
| P7-C02 | done | Implement ingest -> normalize -> dedup pipeline | `news/pipeline.py` + storage tables | Same story from multiple sources is merged by canonical key with provenance retained |
| P7-C03 | done | Implement grounded digest composer | `news/digest.py` + citation policy | Every digest section has source references and confidence markers |
| P7-C04 | done | Add automation template for daily AI digest | `automation/mission_planner.py` template `ai_news_daily` | User can create a daily digest mission in one API call |

### Epic D - Delivery and User Flow

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P7-D01 | done | Add digest artifact + inbox delivery format | `api/news_api.py` + inbox integration + schema | Digest lands in inbox with summary, key links, and action hints |
| P7-D02 | done | Add optional outbound channels (webhook/email/telegram adapter) | delivery adapters + policy controls | User can opt in to external delivery channels with per-channel limits |

### Epic E - Reliability and Security Gates

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P7-E01 | done | Add release gate for news mission E2E flow | `scripts/release/news_mission_gate.py` + CI wiring | CI fails if schedule, ingestion, dedup, or digest citation contracts regress |
| P7-E02 | done | Add security gate for provider session handling | `scripts/security/provider_session_policy_check.py` | CI fails on token leakage, missing revocation checks, or missing auth boundaries |
| P7-E03 | done | Add replay fixtures for deterministic digest output | `eval/fixtures/replay/news/*` + tests | Same input snapshot produces stable digest structure and provenance fields |

## Sprint Plan (2 Sprint Cut)

### Sprint P7-S0 (Foundation, 2 weeks)

| ID | Status | Scope |
|---|---|---|
| P7-A01 | done | Connector contract and normalized source item schema |
| P7-A02 | done | Reddit ingestion connector with retries/rate policy |
| P7-B01 | done | Provider session storage and revoke/list APIs |
| P7-B02 | done | Entitlement resolver endpoint and model capability checks |
| P7-C01 | done | `news_mission_v1` contract + mission plan API |
| P7-C02 | done | Ingest-normalize-dedup storage pipeline |

Sprint goal:
- one scheduled mission can ingest Reddit + web sources and produce a stored normalized corpus for digest composition.

### Sprint P7-S1 (Productization, 2 weeks)

| ID | Status | Scope |
|---|---|---|
| P7-A03 | done | X ingestion connector with policy controls |
| P7-A04 | done | Web fetch/extract hardening |
| P7-B03 | done | Entitlement enforcement in model routing |
| P7-C03 | done | Grounded digest composer with citations |
| P7-C04 | done | `ai_news_daily` automation template |
| P7-D01 | done | Inbox digest delivery UX contract |
| P7-E01 | done | News mission E2E release gate |
| P7-E02 | done | Provider session security gate |
| P7-E03 | done | Replay determinism fixtures |

Sprint goal:
- user receives a daily grounded digest artifact by schedule with release-gated reliability/security checks.

## API and File Map (Implementation Anchors)

Core new modules:
- `sources/base.py`
- `sources/reddit_connector.py`
- `sources/x_connector.py`
- `sources/web_connector.py`
- `news/pipeline.py`
- `news/digest.py`
- `runtime/provider_sessions.py`
- `runtime/entitlements.py`
- `api/source_api.py`
- `api/news_api.py`

Core updates:
- `runtime/server.py` (service wiring + routers)
- `models/model_manager.py` (entitlement gate before cloud provider calls)
- `automation/mission_planner.py` (template `ai_news_daily`)
- `automation/automation_scheduler.py` (mission payload compatibility checks)
- `storage/migrations.py` and `storage/database.py` (source/news/provider-session tables)

Contracts and docs:
- `contracts/news_mission_v1.json`
- `docs/mission-planner.md` (news template extension)
- `docs/developer-quickstart.md` (daily news mission quickstart)
- `docs/security-compliance-baseline.md` (provider session controls)

## KPIs for Phase Exit

- `digest_freshness_p95_min <= 90`
- `mission_success_rate >= 99%`
- `source_ingest_error_rate <= 1%`
- `citation_coverage_rate >= 95%`
- `provider_session_revocation_propagation_p95_sec <= 60`

## Next Checkpoint

- Publish and execute Phase 7 release cut checklist: `docs/phase7-release-cut-checklist.md`.
- Complete DoD sign-off after one green full release pipeline run with attached artifacts.
- Archive `artifacts/phase7-signoff-summary.json` + `artifacts/phase7-signoff-summary.md` as release evidence.
