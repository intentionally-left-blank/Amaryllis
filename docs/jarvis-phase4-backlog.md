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
| P4-B01 | todo | Implement Linux desktop integration pack (notifications/window/clipboard/app launch) | adapter set + tests | Common desktop actions run through policy-gated adapters on Linux |
| P4-B02 | todo | Add macOS staging parity adapters for core desktop actions | mac staging adapters + parity tests | macOS supports critical subset with same contract and policy behavior |
| P4-B03 | todo | Add transaction-safe rollback hints for desktop actions | action rollback contract + receipts | Risky actions provide deterministic rollback metadata where feasible |

### Epic C - Autonomous Multi-Agent Operations

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-C01 | todo | Introduce supervisor for bounded multi-agent task graphs | supervisor runtime + graph contract | Complex goals split into bounded subtasks with parent-level control |
| P4-C02 | todo | Add mission checkpointing and resume across runtime restarts | checkpoint store + resume policy | Long missions recover from crash/restart without silent state corruption |
| P4-C03 | todo | Add per-mission objective verification gates | verifier policies + escalation routes | Mission completion requires explicit objective checks, not only tool success |

### Epic D - Product Reliability and Distribution

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P4-D01 | todo | Add end-to-end user journey benchmark harness | journey benchmark suite + report | Release/nightly include comparable user-flow success/latency KPIs |
| P4-D02 | todo | Add mission outcome public KPI pack v2 (release + nightly) | expanded KPI report schema | Success/recovery metrics include trendable mission-class breakdowns |
| P4-D03 | todo | Harden packaging/update/rollback path for Linux primary and mac staging | updater/rollback contracts + smoke gates | Operator can safely install/update/rollback without manual recovery steps |

## Current Sprint (P4-S0)

| ID | Status | Scope |
|---|---|---|
| P4-A01 | in_progress | unified multimodal session state machine contract (runtime manager + `/flow/sessions/*` API + tests + docs) |
| P4-A02 | in_progress | explicit plan-vs-execute mode in API (`/agents/{agent_id}/runs/dispatch`) + interaction-mode contract endpoint + tests + docs |
| P4-B01 | todo | Linux desktop integration adapters (first slice) |
| P4-C01 | todo | bounded multi-agent supervisor skeleton |
| P4-D01 | todo | end-to-end user journey benchmark baseline |

## Next Checkpoint
- Deliver first executable "Jarvis on PC" flow:
  - unified session states for text/voice/visual interaction,
  - explicit plan-vs-execute control path,
  - Linux desktop adapter baseline under policy guardrails,
  - supervisor skeleton for bounded multi-agent decomposition,
  - initial journey benchmark artifact in release/nightly quality pack.
