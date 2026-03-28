# Jarvis Phase 4 Backlog

## Objective
Move from OSS platform readiness to daily-driver "Jarvis on PC": unified multimodal user flow, desktop action surface, and bounded L5-grade autonomy operations.

## Status Legend
- `todo`
- `in_progress`
- `done`
- `blocked`

## Tier-1 Exit Criteria (Phase 4)
- User flow is unified and production-ready: "say/type intent -> get plan -> approve/auto-run -> observe result -> iterate".
- Linux-first desktop integration covers core PC control domains under strict capability and permission policy.
- Multi-agent orchestration executes long missions with checkpoints, retries, and bounded budgets.
- Release and nightly pipelines expose end-to-end product KPIs for user-flow success and recovery quality.
- Distribution path (installer/update/rollback) is reliable for Linux primary and macOS staging.
- Generation loop behavior is portable across CPU/GPU/NPU profiles with deterministic fallback semantics.
- RAG/tooling stack is provenance-first and zero-trust by default (injection containment and sandbox guarantees).
- Quantized model delivery is reproducible and attestable (quant passport, signatures, and environment passport).
- First-run activation path is low-friction (hardware autodetect + ready profiles + model package UX) with target time-to-first-answer under 10 minutes.
- Distribution channels cover mainstream desktop discovery paths (GitHub Releases + WinGet + Homebrew + Flathub) with signed artifacts.
- Privacy contract is explicit and user-visible: offline-by-default behavior, clear network intent, and opt-in telemetry only.
- Developer adoption path is productized: OpenAI-compatible local API, stable SDK quickstarts, and integration samples.
- RU/EN localization, governance, and contributor workflow are mature enough for sustained OSS ecosystem growth.

## Epics and Tasks

### Epic A - Multimodal User Flow

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-A01 | done | Define unified session state machine for text/voice/visual loop | session contract + API/UI integration | User can move across listen/plan/act/review states without context loss |
| P4-A02 | done | Add "plan or execute" explicit interaction mode | reasoning/plan mode API + UI control | User can choose plan-first vs direct execution with clear trust boundaries |
| P4-A03 | done | Add action timeline and plain-language explainability feed | execution timeline stream + explain payload | Every action has visible reason, result, and next-step suggestion |

### Epic B - Desktop Action Surface (Linux-First)

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-B01 | done | Implement Linux desktop integration pack (notifications/window/clipboard/app launch) | adapter set + tests | Common desktop actions run through policy-gated adapters on Linux |
| P4-B02 | done | Add macOS staging parity adapters for core desktop actions | mac staging adapters + parity tests | macOS supports critical subset with same contract and policy behavior |
| P4-B03 | done | Add transaction-safe rollback hints for desktop actions | action rollback contract + receipts | Risky actions provide deterministic rollback metadata where feasible |

### Epic C - Autonomous Multi-Agent Operations

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-C01 | done | Introduce supervisor for bounded multi-agent task graphs | supervisor runtime + graph contract | Complex goals split into bounded subtasks with parent-level control |
| P4-C02 | done | Add mission checkpointing and resume across runtime restarts | checkpoint store + resume policy | Long missions recover from crash/restart without silent state corruption |
| P4-C03 | done | Add per-mission objective verification gates | verifier policies + escalation routes | Mission completion requires explicit objective checks, not only tool success |

### Epic D - Product Reliability and Distribution

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-D01 | done | Add end-to-end user journey benchmark harness | journey benchmark suite + report | Release/nightly include comparable user-flow success/latency KPIs |
| P4-D02 | done | Add mission outcome public KPI pack v2 (release + nightly) | expanded KPI report schema | Success/recovery metrics include trendable mission-class breakdowns |
| P4-D03 | done | Harden packaging/update/rollback path for Linux primary and mac staging | updater/rollback contracts + smoke gates | Operator can safely install/update/rollback without manual recovery steps |

### Epic E - Runtime Portability and QoS Envelope

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-E01 | done | Define backend-portable generation-loop contract (`prefill/decode`, cache, fallback semantics) | contract spec + conformance tests | CPU/GPU/NPU backends pass the same functional contract and fallback determinism checks |
| P4-E02 | done | Add KV cache observability and pressure-policy framework | KV telemetry schema + policy engine | Runtime emits KV pressure signals and applies policy transitions without silent quality collapse |
| P4-E03 | in_progress | Implement QoS governor (`TTFT`, sustained decode, thermal-aware mode switching) | qos governor module + benchmark hooks | User-visible modes maintain target latency/stability envelopes under stress |
| P4-E04 | done | Add long-context reliability eval pack | eval dataset + gate job | Release/nightly fail on long-context regressions in relevance and stability |

### Epic F - Trust, Safety, and Supply Chain Hardening

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-F01 | in_progress | Make provenance mandatory for RAG-grounded answers | provenance contract + UI/API exposure | Responses using external context include verifiable source trace by default |
| P4-F02 | in_progress | Enforce zero-trust tool execution and unsafe-deserialization bans | hardened executor + security policy tests | Tool chain blocks known unsafe deserialization patterns and enforces sandbox permissions |
| P4-F03 | in_progress | Build injection-resilience regression suite for RAG and agent flows | attack scenarios + CI gate | Release/nightly publish containment score and block severe regressions |
| P4-F04 | done | Introduce secure model package + quantization passport | signed artifact spec + validator tooling | Model artifacts fail admission without signatures, hashes, and quant recipe metadata |

### Epic G - Reproducibility, Licensing, and Personalization Discipline

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-G01 | in_progress | Add runtime environment passport generation (hardware, drivers, runtime, quant recipe) | env passport artifact + collector | Every benchmark and release bundle contains environment passport metadata |
| P4-G02 | in_progress | Add license admission policy for models/adapters/index packs | license policy engine + report | Artifact onboarding is blocked on incompatible licensing constraints |
| P4-G03 | todo | Add adapter-based personalization lane with rollback and signature checks | personalization workflow + adapter registry | Personalization uses reversible adapter stacks; base weights remain immutable in default path |

### Epic H - Mass Adoption, Distribution, and Ecosystem

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-H01 | done | Add first-run onboarding profiles with hardware autodetect (`fast/balanced/quality`) | onboarding wizard + profile selector + tests | New user reaches first successful response with recommended profile and no manual quant tuning |
| P4-H02 | done | Ship model catalog as package UX (requirements + one-click install) | model package registry + UI/API contract | Users select models by capability/system requirements instead of raw artifact filenames |
| P4-H03 | done | Implement privacy/offline transparency contract | offline indicator + network intent panel + policy docs | User can see when network is required; telemetry remains opt-in by default |
| P4-H04 | done | Expand distribution channels for mass discovery | WinGet/Homebrew/Flathub pipeline + channel docs | Stable channel artifacts are published and verifiable for Windows/macOS/Linux discovery paths |
| P4-H05 | done | Productize developer adoption path | OpenAI-compatible API starter pack + SDK quickstarts + integration examples | Dev can run local API and complete first integration in under 15 minutes |
| P4-H06 | done | Add RU/EN localization and template packs | i18n baseline + localized docs + starter prompts/workflows | RU and EN users have first-class UI/docs/templates with release-level coverage checks |
| P4-H07 | done | Harden OSS governance and contribution policy | licensing/trademark policy + DCO/CoC + maintainer map + release discipline | External contributors onboard without legal ambiguity and release governance is explicit |
| P4-H08 | done | Add adoption KPI funnel and growth dashboards | install/activation/retention/feature-adoption metrics + dashboard | Team can trace product changes to adoption impact while preserving privacy constraints |

## Current Sprint (P4-S0)

| ID | Status | Scope |
|---|---|---|
| P4-A01 | done | unified multimodal session state machine contract (runtime manager + `/flow/sessions/*` API + tests + docs) |
| P4-A02 | done | explicit plan-vs-execute mode in API (`/agents/{agent_id}/runs/dispatch`) + interaction-mode contract endpoint + tests + docs |
| P4-A03 | done | action timeline stream + explainability payload (`/agents/runs/{run_id}/events` + `/agents/runs/{run_id}/explain`) + tests + docs |
| P4-B01 | done | Linux desktop integration adapters (first slice: `desktop_action` tool + Linux/Stub adapters + tests + docs) |
| P4-B02 | done | macOS staging parity adapters (`MacOSDesktopActionAdapter`) + platform selector wiring + adapter contract tests |
| P4-B03 | done | desktop action rollback-hint contract (`metadata.rollback_hint` + terminal receipts + release/nightly rollback gate) |
| P4-C01 | done | bounded multi-agent supervisor runtime contract (task graph manager + API contract + launch/tick control loop + release/nightly gate) |
| P4-C02 | done | supervisor checkpoint store + auto-hydrate on runtime start (SQLite migration + storage methods + recovery tests + release/nightly gate) |
| P4-C03 | done | objective verification gates in supervisor (`objective_verification` policy + `/supervisor/graphs/{id}/verify` endpoint + release/nightly gate) |
| P4-D01 | done | end-to-end user journey benchmark baseline (`scripts/release/user_journey_benchmark.py` + baseline + release/nightly artifact wiring + strict KPI thresholds) |
| P4-D02 | done | mission KPI pack schema v2 (`mission_success_recovery_report_pack_v2` + class breakdown by mission/recovery/quality/user_flow/nightly + release/nightly gate) |
| P4-D03 | done | distribution resilience report (`scripts/release/build_distribution_resilience_report.py`) + release-gate blocking artifact wiring (`distribution-resilience-report.json`) |

## Planned Sprint (P4-S1, Research Integration Hardening)

| ID | Status | Scope |
|---|---|---|
| P4-E01 | done | generation-loop portability contract + backend conformance matrix (`/models/generation-loop/contract` + release/nightly conformance gate) |
| P4-E02 | done | KV telemetry schema + initial pressure-policy transitions |
| P4-E03 | in_progress | QoS governor baseline with `balanced` and `power-save` modes |
| P4-E04 | done | long-context reliability eval dataset + release/nightly blocking gate |
| P4-F01 | in_progress | provenance contract for RAG responses + API payload wiring |
| P4-F02 | in_progress | unsafe-deserialization denylist checks + sandbox policy tests |
| P4-F03 | in_progress | injection-containment regression gate + release/nightly artifact wiring |
| P4-F04 | done | signed model artifact and quant-passport validator MVP + admission gate wiring |
| P4-G01 | in_progress | environment passport collector in release/nightly artifacts |
| P4-G02 | in_progress | license admission checker for model/adapters onboarding |

## Planned Sprint (P4-S2, Mass Adoption Foundation)

| ID | Status | Scope |
|---|---|---|
| P4-H01 | done | first-run onboarding wizard + hardware profile recommendation contract |
| P4-H02 | done | model package catalog and system-requirement-aware install flow |
| P4-H03 | done | offline/privacy transparency controls in runtime + UX |
| P4-H04 | done | channel packaging pipeline for WinGet/Homebrew/Flathub |
| P4-H05 | done | developer quickstart pack for OpenAI-compatible API integration |
| P4-H06 | done | RU/EN localization baseline and template starter packs |
| P4-H07 | done | governance baseline (license/trademark/DCO/CoC/release conventions) |
| P4-H08 | done | adoption KPI funnel and privacy-safe analytics surface |

## Execution Playbook (Start-Now)
- Detailed implementation-ready program: `/Users/bogdan/Amaryllis/docs/jarvis-phase4-execution-plan.md`
- Sprint order: `P4-S1` (contracts/baselines) -> `P4-S2` (enforcement/gates) -> `P4-S3` (hardening/parity) -> `P4-S4` (safe personalization lane)

### Critical Path (Implementation Order)
1. `P4-E01` generation-loop contract and conformance matrix
2. `P4-E02` KV telemetry and pressure-policy transitions
3. `P4-E03` QoS governor mode switching
4. `P4-E04` long-context reliability gate pack
5. `P4-F01` provenance-by-default responses
6. `P4-F02` zero-trust tool execution + unsafe-deserialization bans
7. `P4-F03` injection-resilience regression suite
8. `P4-F04` secure model package + quant passport admission
9. `P4-G01` environment passport in release/nightly artifacts
10. `P4-G02` license admission policy for onboarding
11. `P4-G03` adapter-based personalization with rollback/signature checks

### Mass Adoption Path (Parallel After Core P0 Gates)
1. `P4-H01` first-run onboarding profiles with hardware autodetect
2. `P4-H02` model catalog packages and one-click install
3. `P4-H03` offline/privacy transparency and opt-in telemetry contract
4. `P4-H04` WinGet/Homebrew/Flathub channel publishing
5. `P4-H05` developer API starter pack and integration examples
6. `P4-H06` RU/EN localization and template packs
7. `P4-H07` OSS governance and licensing clarity package
8. `P4-H08` adoption KPI funnel and growth dashboards

### Start Conditions
- No P0 task may be skipped out of critical-path order unless dependency is explicitly removed in this backlog.
- New gates enter warning mode first, then become blocking after 3 stable nightlies.
- Linux primary gates must be green before macOS staging parity sign-off.

## Next Checkpoint
- Deliver first executable "Jarvis on PC" flow:
  - unified session states for text/voice/visual interaction,
  - explicit plan-vs-execute control path,
  - Linux desktop adapter baseline under policy guardrails,
  - supervisor skeleton for bounded multi-agent decomposition,
  - initial journey benchmark artifact in release/nightly quality pack,
  - Linux distribution resilience gate for install/upgrade/rollback reliability.
- Start Tier-1 research-integration hardening:
  - generation-loop portability contract and first backend conformance checks,
  - KV/QoS observability baseline (`TTFT`, decode stability, cache pressure),
  - provenance-by-default RAG answers and zero-trust tool chain checks,
  - signed artifact + quant passport + environment passport baseline in CI.
