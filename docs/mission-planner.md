# Mission Planner API

## Purpose

`POST /automations/mission/plan` builds a risk-aware automation mission plan before creating a scheduler entry.

It combines:

- dry-run simulation (`agent_manager.simulate_run`),
- cadence normalization (`workday/daily/hourly/weekly/watch_fs`),
- recommendation gate for immediate start based on mission risk.

## Endpoint

```bash
curl -X POST http://localhost:8000/automations/mission/plan \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent_id>",
    "user_id": "user-001",
    "message": "Run autonomous daily code health mission",
    "cadence_profile": "workday",
    "timezone": "UTC",
    "start_immediately": true
  }'
```

## Request Fields

- `agent_id` (required): target agent.
- `user_id` (required): owner user id.
- `message` (required): mission instruction.
- `session_id` (optional): session context.
- `timezone` (default `UTC`): planner timezone.
- `cadence_profile` (default `workday`): one of `hourly`, `daily`, `workday`, `weekly`, `watch_fs`.
- `start_immediately` (default `false`): requested immediate scheduling.
- `schedule_type`, `schedule`, `interval_sec` (optional): explicit schedule override.
- `max_attempts`, `budget` (optional): passed to dry-run simulation only.

## Response Shape

- `mission_plan`
  - normalized schedule (`schedule_type`, `schedule`, `interval_sec`, `next_run_at`),
  - `risk.overall` and `risk.requires_review`,
  - recommendation (`requested_start_immediately`, `effective_start_immediately`, checklist),
  - `apply_payload` compatible with `POST /automations/create`.
- `simulation`: full dry-run simulation payload.
- `apply_hint`: `{ endpoint: "/automations/create", payload: ... }`.

## Behavior

- High/critical/unknown mission risk forces `effective_start_immediately=false`.
- For low/medium risk, `effective_start_immediately` follows user request.
- `watch_fs` cadence requires explicit `schedule` payload with `path` and polling settings.

## Related Tests

- `tests/test_mission_planner.py`
- `tests/test_automation_mission_plan_api.py`
