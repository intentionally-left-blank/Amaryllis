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

## Epics and Tasks

### Epic A - Multimodal User Flow

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-A01 | in_progress | Define unified session state machine for text/voice/visual loop | session contract + API/UI integration | User can move across listen/plan/act/review states without context loss |
| P4-A02 | in_progress | Add "plan or execute" explicit interaction mode | reasoning/plan mode API + UI control | User can choose plan-first vs direct execution with clear trust boundaries |
| P4-A03 | todo | Add action timeline and plain-language explainability feed | execution timeline stream + explain payload | Every action has visible reason, result, and next-step suggestion |

### Epic B - Desktop Action Surface (Linux-First)

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-B01 | in_progress | Implement Linux desktop integration pack (notifications/window/clipboard/app launch) | adapter set + tests | Common desktop actions run through policy-gated adapters on Linux |
| P4-B02 | done | Add macOS staging parity adapters for core desktop actions | mac staging adapters + parity tests | macOS supports critical subset with same contract and policy behavior |
| P4-B03 | todo | Add transaction-safe rollback hints for desktop actions | action rollback contract + receipts | Risky actions provide deterministic rollback metadata where feasible |

### Epic C - Autonomous Multi-Agent Operations

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-C01 | in_progress | Introduce supervisor for bounded multi-agent task graphs | supervisor runtime + graph contract | Complex goals split into bounded subtasks with parent-level control |
| P4-C02 | in_progress | Add mission checkpointing and resume across runtime restarts | checkpoint store + resume policy | Long missions recover from crash/restart without silent state corruption |
| P4-C03 | in_progress | Add per-mission objective verification gates | verifier policies + escalation routes | Mission completion requires explicit objective checks, not only tool success |

### Epic D - Product Reliability and Distribution

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-D01 | in_progress | Add end-to-end user journey benchmark harness | journey benchmark suite + report | Release/nightly include comparable user-flow success/latency KPIs |
| P4-D02 | in_progress | Add mission outcome public KPI pack v2 (release + nightly) | expanded KPI report schema | Success/recovery metrics include trendable mission-class breakdowns |
| P4-D03 | done | Harden packaging/update/rollback path for Linux primary and mac staging | updater/rollback contracts + smoke gates | Operator can safely install/update/rollback without manual recovery steps |

### Epic E - Runtime Portability and QoS Envelope

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-E01 | todo | Define backend-portable generation-loop contract (`prefill/decode`, cache, fallback semantics) | contract spec + conformance tests | CPU/GPU/NPU backends pass the same functional contract and fallback determinism checks |
| P4-E02 | todo | Add KV cache observability and pressure-policy framework | KV telemetry schema + policy engine | Runtime emits KV pressure signals and applies policy transitions without silent quality collapse |
| P4-E03 | todo | Implement QoS governor (`TTFT`, sustained decode, thermal-aware mode switching) | qos governor module + benchmark hooks | User-visible modes maintain target latency/stability envelopes under stress |
| P4-E04 | todo | Add long-context reliability eval pack | eval dataset + gate job | Release/nightly fail on long-context regressions in relevance and stability |

### Epic F - Trust, Safety, and Supply Chain Hardening

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-F01 | todo | Make provenance mandatory for RAG-grounded answers | provenance contract + UI/API exposure | Responses using external context include verifiable source trace by default |
| P4-F02 | todo | Enforce zero-trust tool execution and unsafe-deserialization bans | hardened executor + security policy tests | Tool chain blocks known unsafe deserialization patterns and enforces sandbox permissions |
| P4-F03 | todo | Build injection-resilience regression suite for RAG and agent flows | attack scenarios + CI gate | Release/nightly publish containment score and block severe regressions |
| P4-F04 | todo | Introduce secure model package + quantization passport | signed artifact spec + validator tooling | Model artifacts fail admission without signatures, hashes, and quant recipe metadata |

### Epic G - Reproducibility, Licensing, and Personalization Discipline

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-G01 | todo | Add runtime environment passport generation (hardware, drivers, runtime, quant recipe) | env passport artifact + collector | Every benchmark and release bundle contains environment passport metadata |
| P4-G02 | todo | Add license admission policy for models/adapters/index packs | license policy engine + report | Artifact onboarding is blocked on incompatible licensing constraints |
| P4-G03 | todo | Add adapter-based personalization lane with rollback and signature checks | personalization workflow + adapter registry | Personalization uses reversible adapter stacks; base weights remain immutable in default path |

## Current Sprint (P4-S0)

| ID | Status | Scope |
|---|---|---|
| P4-A01 | in_progress | unified multimodal session state machine contract (runtime manager + `/flow/sessions/*` API + tests + docs) |
| P4-A02 | in_progress | explicit plan-vs-execute mode in API (`/agents/{agent_id}/runs/dispatch`) + interaction-mode contract endpoint + tests + docs |
| P4-B01 | in_progress | Linux desktop integration adapters (first slice: `desktop_action` tool + Linux/Stub adapters + tests + docs) |
| P4-B02 | done | macOS staging parity adapters (`MacOSDesktopActionAdapter`) + platform selector wiring + adapter contract tests |
| P4-C01 | in_progress | bounded multi-agent supervisor skeleton (task graph manager + API contract + launch/tick control loop + tests) |
| P4-C02 | in_progress | supervisor checkpoint store + auto-hydrate on runtime start (SQLite migration + storage methods + recovery tests) |
| P4-C03 | in_progress | objective verification gates in supervisor (`objective_verification` policy + `/supervisor/graphs/{id}/verify` endpoint + tests) |
| P4-D01 | in_progress | end-to-end user journey benchmark baseline (`scripts/release/user_journey_benchmark.py` + baseline + release/nightly artifact wiring) |
| P4-D02 | in_progress | mission KPI pack schema v2 (`mission_success_recovery_report_pack_v2` + class breakdown by mission/recovery/quality/user_flow/nightly) |
| P4-D03 | done | distribution resilience report (`scripts/release/build_distribution_resilience_report.py`) + release-gate blocking artifact wiring (`distribution-resilience-report.json`) |

## Planned Sprint (P4-S1, Research Integration Hardening)

| ID | Status | Scope |
|---|---|---|
| P4-E01 | todo | generation-loop portability contract draft + backend conformance matrix |
| P4-E02 | todo | KV telemetry schema + initial pressure-policy transitions |
| P4-E03 | todo | QoS governor baseline with `balanced` and `power-save` modes |
| P4-F01 | todo | provenance contract for RAG responses + API payload wiring |
| P4-F02 | todo | unsafe-deserialization denylist checks + sandbox policy tests |
| P4-F04 | todo | signed model artifact and quant-passport validator MVP |
| P4-G01 | todo | environment passport collector in release/nightly artifacts |
| P4-G02 | todo | license admission checker for model/adapters onboarding |

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
