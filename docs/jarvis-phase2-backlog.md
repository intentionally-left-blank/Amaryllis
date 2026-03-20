# Jarvis Phase 2 Backlog

## Objective
Reach Tier-1 operator readiness: modular cognitive kernel, deterministic reproducibility chain, and enforceable non-functional quality gates for autonomous operation.

## Status Legend
- `todo`
- `in_progress`
- `done`
- `blocked`

## Tier-1 Exit Criteria
- Modular kernel boundaries are enforced by import/boundary gates and adapter contracts; no cross-layer violations in CI.
- Reproducibility path is deterministic on clean machine and CI: pinned toolchains, locked dependencies, versioned runtime profiles, and provenance artifacts.
- Non-functional gates block regressions in latency, run success, and reliability under fault/load scenarios.
- L2-L3 autonomy policies are explicit, testable, and auditable with rollback-safe action flow.
- Linux-first runtime is production-ready with documented install/upgrade path and parity checklist vs macOS staging app.

## Epics and Tasks

### Epic A - Cognitive Kernel Modularization

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P2-A01 | done | Define cognitive kernel module contracts | ADR + `kernel/` contract interfaces | Planner/executor/memory/tool-router contracts are versioned and consumed via interfaces only |
| P2-A02 | done | Extract orchestration core into modular kernel package | `kernel/orchestration/` + adapters | Runtime/API layers call kernel adapters, not internal orchestration classes directly |
| P2-A03 | todo | Add backend cognition adapter abstraction (LLM/post-LLM swappable) | backend adapter contract + compatibility tests | At least two backends pass same contract suite with no API-layer changes |
| P2-A04 | done | Enforce architecture boundaries in CI for kernel stack | boundary-check rules + workflow gate | CI blocks forbidden imports across API/kernel/storage/UI layers |

### Epic B - Deterministic Reproducibility Chain

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P2-B01 | todo | Pin full toolchain matrix (Python/Swift/system deps) | versioned toolchain manifest | Local bootstrap and CI resolve the same toolchain versions deterministically |
| P2-B02 | done | Introduce runtime profile manifests (`dev/ci/release`) | profile schema + validation | Runtime fails fast on missing/invalid profile values; profile drift check in CI |
| P2-B03 | todo | Make eval/replay deterministic via seeds and fixture snapshots | deterministic eval mode + fixture policy | Same commit/profile yields same eval output within declared tolerance |
| P2-B04 | todo | Add release provenance and SBOM generation | provenance artifact + SBOM in release pipeline | Each release artifact has signed provenance and dependency inventory |

### Epic C - Performance, SLO, and Reliability Gates

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P2-C01 | done | Version SLO profiles and quality budgets | `slo_profiles/*.json` + docs | SLO targets and error-budget policy are versioned and environment-scoped |
| P2-C02 | done | Add blocking perf gate for critical paths (chat/run/voice/stream) | CI perf workflow + thresholds | PR fails on p95/error regressions above budget for critical paths |
| P2-C03 | todo | Add fault-injection reliability gate | chaos test suite + report | Retry/recovery behavior is validated for provider/tool/network fault classes |
| P2-C04 | todo | Add concurrency/load gate for mission queue | load test harness + SLO assertions | Queue stability and success-rate gates pass at target concurrent load |
| P2-C05 | todo | Add SLO burn-rate regression gate for nightly runs | nightly burn-rate trend job | Nightly pipeline flags sustained error-budget burn anomalies automatically |

### Epic D - Trust and Autonomy Controls (L2-L3)

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P2-D01 | todo | Define policy-pack contract for autonomy levels | policy schema + validators | L2/L3 behavior is policy-driven, not hardcoded, with strict validation |
| P2-D02 | todo | Add mission simulation mode before autonomous execution | simulation endpoint/UI + dry-run receipts | User can preview full action plan, risk tags, and rollback hints before apply |
| P2-D03 | todo | Add dynamic mission budgets with guardrail escalations | budget controller + escalation policies | Budget breaches produce deterministic pause/escalation/kill-switch behavior |
| P2-D04 | todo | Add audit timeline UX for autonomous actions | mission audit panel + export | Every autonomous action chain is inspectable/exportable with actor/policy context |

### Epic E - Linux-First Productization

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P2-E01 | done | Define Linux parity matrix and acceptance tests | parity checklist + CI smoke | Linux runtime passes mandatory parity checks for run/voice/tools/observability |
| P2-E02 | todo | Package Linux runtime with reproducible installer path | installer scripts + docs | Fresh Linux machine can install/run/upgrade via documented deterministic path |
| P2-E03 | todo | Add release channels and safe upgrade/rollback flow | channel strategy + rollback tests | Stable/canary channels support verified rollback without data loss |

## Current Sprint (Sprint P2-S1)

| ID | Status | Scope |
|---|---|---|
| P2-A01 | done | cognitive kernel interface contracts and ADR |
| P2-A02 | done | orchestration core extracted into `kernel/orchestration` and runtime adapter wiring |
| P2-A04 | done | kernel boundary rules + CI enforcement |
| P2-B02 | done | runtime profile schema (`dev/ci/release`) and fail-fast validation |
| P2-C01 | done | versioned SLO profiles and quality budget policy |
| P2-C02 | done | blocking perf gate for chat/run/voice/stream critical paths |
| P2-E01 | done | Linux parity matrix and acceptance smoke gates |

## Next Checkpoint
- Deliver sprint result with:
  - approved kernel contract ADR and initial `kernel` interface package,
  - orchestration core extraction under `kernel/orchestration` with adapter wiring at runtime boundaries,
  - profile schema and validation path wired into runtime startup,
  - first versioned SLO profile set with budget policy documented,
  - baseline CI perf gate for at least run + stream paths,
  - Linux parity checklist with automated smoke report artifact.
