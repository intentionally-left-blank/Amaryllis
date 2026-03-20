# Dynamic Mission Budgets

## Purpose

Mission runs enforce hard runtime budgets:

- `max_tokens`
- `max_duration_sec`
- `max_tool_calls`
- `max_tool_errors`

Budget usage is validated live during checkpoints and before each attempt.

## Guardrail Escalation Policy (current)

Budget breach behavior is deterministic:

1. first budget breach for run history:
   - run ends with `status=failed`
   - `stop_reason=budget_guardrail_paused`
   - checkpoint stage: `budget_guardrail_paused`
   - operator can fix scope/budget and call resume
2. repeated budget breach for same run history:
   - run ends with `status=canceled`
   - `stop_reason=budget_guardrail_kill_switch`
   - checkpoint stage: `budget_guardrail_escalated`
   - agent-scope kill switch is triggered for sibling `queued/running` runs
   - checkpoint stage: `budget_guardrail_kill_switch_scope`

Scope of escalation kill switch:

- same `user_id`
- same `agent_id`
- current run is excluded

## Deterministic Operator Flow

1. Create run with mission budget (`POST /agents/{agent_id}/runs`).
2. First budget breach:
   - terminal state is deterministic:
     - `status=failed`
     - `failure_class=budget_exceeded`
     - `stop_reason=budget_guardrail_paused`
   - replay contains `stage=budget_guardrail_paused`
3. Operator inspects evidence (`/replay`, `/diagnostics`, `/audit`) and decides to resume.
4. If the same run breaches budget again after resume:
   - terminal state escalates deterministically:
     - `status=canceled`
     - `stop_reason=budget_guardrail_kill_switch`
   - replay contains:
     - `stage=budget_guardrail_escalated`
     - `stage=budget_guardrail_kill_switch_scope`
   - sibling `queued/running` runs in same `(user_id, agent_id)` scope are canceled with `stop_reason=kill_switch_triggered`.

## API Notes

- `POST /agents/{agent_id}/runs` accepts `budget` values.
- budget breach diagnostics are visible in:
  - `GET /agents/runs/{run_id}`
  - `GET /agents/runs/{run_id}/replay`
  - `GET /agents/runs/{run_id}/diagnostics`
  - `GET /agents/runs/{run_id}/audit`
- `POST /agents/runs/{run_id}/resume` requeues the same run history for post-fix retry.

## Test Coverage

- `tests/test_agent_run_manager.py::test_run_budget_tool_calls_exceeded_fails_fast`
- `tests/test_agent_run_manager.py::test_repeated_budget_breach_escalates_to_agent_scope_kill_switch`
- `tests/test_agent_run_budget_guardrail_api.py::test_single_budget_breach_pauses_without_retry`
- `tests/test_agent_run_budget_guardrail_api.py::test_repeated_budget_breach_escalates_to_agent_scope_kill_switch`
