# Jarvis Phase 0 Backlog

## Objective
Harden architecture, delivery reproducibility, and quality gates to support future full-autonomy work.

## Status Legend
- `todo`
- `in_progress`
- `done`
- `blocked`

## Epics and Tasks

### Epic A - Runtime Architecture Decomposition

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| A-01 | done | Replace deprecated FastAPI shutdown event with lifespan lifecycle | `runtime/server.py` migrated | No `on_event("shutdown")` usage remains; service shutdown flow unchanged in tests |
| A-02 | todo | Split `TaskExecutor` into orchestration + step executors package | `tasks/execution/` modules | Existing tests pass; no behavior regression in `tests/test_task_executor.py` |
| A-03 | todo | Extract run retry/replan policy to dedicated module | `agents/run_policy.py` | `AgentRunManager` no longer contains policy branching internals |
| A-04 | todo | Introduce import-boundary checks for core layers | boundary-check script + CI step | CI blocks forbidden imports between API/orchestration/storage layers |

### Epic B - Reproducible Build and Release Chain

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| B-01 | done | Add deterministic dependency lock process | `requirements.lock` + generation script | CI installs from lock file in release/security gates |
| B-02 | done | Pin Python patch-version for all gates | workflow updates | All gate workflows run same Python patch version |
| B-03 | done | Add dependency drift check | `scripts/release/check_dependency_drift.py` | CI fails when `requirements.txt` and lock are out of sync |
| B-04 | done | Publish reproducible local bootstrap path | docs + helper script | New contributor can reproduce env from clean machine with single documented path |

### Epic C - Golden Tasks and Non-Functional Gates

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| C-01 | done | Define 20 golden developer tasks | `eval/golden_tasks/dev_v1.json` | Tasks include expected outcome format and pass criteria |
| C-02 | done | Create eval harness runner | `scripts/eval/run_golden_tasks.py` | Produces machine-readable report with pass/fail and durations |
| C-03 | done | Add blocking smoke perf gate for PR | workflow job + thresholds | PR fails on p95 latency and error-rate regressions above budget |
| C-04 | done | Add nightly extended reliability run | scheduled workflow | Nightly report with trend deltas for success/latency/stability |

### Epic D - Trust and Autonomy Controls

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| D-01 | done | Introduce autonomy level config contract (L0-L5) | config schema + docs | Runtime enforces policy level at action execution boundary |
| D-02 | done | Add explicit high-risk action receipts | persisted action receipts | Every high-risk action includes actor, policy level, and rollback hint |
| D-03 | todo | Add kill-switch endpoint and CLI command | service endpoint + script | Ongoing runs can be interrupted immediately and deterministically |

## Current Sprint (Sprint P0-S1)

| ID | Status | Scope |
|---|---|---|
| A-01 | done | Lifecycle hardening via FastAPI lifespan |
| B-01 | done | lock strategy, lock-file bootstrap, and CI wiring |
| B-02 | done | Python patch-version pinning across gates |
| B-03 | done | dependency drift guardrail in CI |
| B-04 | done | reproducible local bootstrap script + docs |
| C-01 | done | golden task taxonomy and initial dataset |
| C-02 | done | initial eval harness and report format |
| C-03 | done | blocking perf smoke gate (p95 + error-rate thresholds) |
| C-04 | done | scheduled nightly reliability suite + trend-delta report artifact |
| D-01 | done | autonomy level contract + runtime execution-boundary enforcement |
| D-02 | done | high-risk action receipt contract + API/audit coverage |

## Next Checkpoint
- Deliver sprint result with:
  - baseline run report from golden tasks suite,
  - deterministic high-risk action receipt baseline sample (`D-02`),
  - deterministic kill-switch surface draft (`D-03`).
