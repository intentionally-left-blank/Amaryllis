# Unified Flow Session Contract

## Purpose

`/flow/sessions/*` defines a single session lifecycle for multimodal interaction (text/voice/visual) so client UI can keep context while user moves between listen/plan/act/review loops.

## Contract Surface

- `GET /flow/sessions/contract`
- `POST /flow/sessions/start`
- `GET /flow/sessions`
- `GET /flow/sessions/{session_id}`
- `POST /flow/sessions/{session_id}/transition`
- `POST /flow/sessions/{session_id}/activity`

All endpoints are user-scoped (or admin-scoped) and include request IDs for traceability.

## States

Allowed session states:

- `created`
- `listening`
- `planning`
- `acting`
- `reviewing`
- `closed`

Transition model is explicit and validated. Invalid transitions are rejected with `validation_error`.

## Channels

Allowed channels:

- `text`
- `voice`
- `visual`

Session channels are fixed at creation time. Activity on non-enabled channels is rejected.

## Telemetry and Audit

Session payload includes:

- transition history (`transitions`)
- per-channel counters (`channel_activity`)
- lifecycle timestamps (`created_at`, `updated_at`, `closed_at`, `duration_ms`)
- telemetry counters (`events_emitted`, `transition_count`, `last_state`, `last_actor`)

Security action receipts are generated for mutating operations.

## Minimal User Flow

1. Client starts flow session with required channels.
2. Client transitions state according to UI stage (`listening` -> `planning` -> `acting` -> `reviewing`).
3. Client records per-channel events (prompt submitted, audio chunk, screenshot attached, etc.).
4. Client closes session when loop ends.

This provides one stable session envelope for multimodal UX and observability.
