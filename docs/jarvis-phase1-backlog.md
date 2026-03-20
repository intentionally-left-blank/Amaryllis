# Jarvis Phase 1 Backlog

## Objective
Deliver Developer Jarvis Alpha with reliable async missions, actionable diagnostics, and first-class local workflow ergonomics.

## Status Legend
- `todo`
- `in_progress`
- `done`
- `blocked`

## Epics and Tasks

### Epic A - Async Mission Reliability and Diagnostics

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P1-A01 | done | Add compact mission diagnostics endpoint | `GET /agents/runs/{run_id}/diagnostics` | Returns deterministic warnings/signals/recommended actions for each run with owner-scope enforcement |
| P1-A02 | done | Add diagnostics package export | script + API payload contract | Export includes replay snapshot, diagnostics summary, and issue/tool evidence bundle |
| P1-A03 | todo | Add mission timeline filter presets in backend | replay filter API options | Replay payload supports server-side stage/status filtering for low-latency UI usage |

### Epic B - Action Layer V1 (Developer Workflows)

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P1-B01 | todo | Add terminal action receipt model | persisted terminal action records | Every terminal command action has audit receipt, actor, policy level, and rollback hint |
| P1-B02 | todo | Add filesystem patch preview mode | dry-run patch planner | File mutations can be previewed and approved as structured diff before execution |
| P1-B03 | todo | Add browser action adapter contract | browser tool interface + stub implementation | Orchestration can call browser actions through typed adapter without coupling to provider implementation |

### Epic C - Visual Mission HUD Foundation

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P1-C01 | todo | Add run event stream endpoint | SSE/WebSocket run stream API | UI receives low-latency run status/checkpoint updates without polling loops |
| P1-C02 | todo | Add mission diagnostics pane in macOS app | run details + warning chips UI | User can inspect timeline, warnings, and recommended actions from one screen |

### Epic D - Voice Push-To-Talk Foundation

| ID | Status | Task | Deliverable | Definition of Done |
|---|---|---|---|---|
| P1-D01 | todo | Add voice session contract | runtime voice session API schema | Start/stop PTT session with explicit state transitions and telemetry |
| P1-D02 | todo | Integrate local STT adapter (pluggable) | `voice/stt_adapter.py` + tests | Adapter interface supports at least one local backend with graceful unavailable mode |

## Current Sprint (Sprint P1-S1)

| ID | Status | Scope |
|---|---|---|
| P1-A01 | done | compact run diagnostics endpoint + ownership enforcement |
| P1-A02 | done | diagnostics package export contract and artifact schema |
| P1-A03 | in_progress | replay filter API options for low-latency HUD integration |
| P1-C01 | todo | event-stream contract for mission HUD |

## Next Checkpoint
- Deliver sprint result with:
  - run replay filter API contract proposal and first implementation slice,
  - updated API docs and regression tests for mission diagnostics surfaces.
