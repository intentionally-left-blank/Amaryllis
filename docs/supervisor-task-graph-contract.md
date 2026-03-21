# Supervisor Task Graph Contract

## Goal
Provide a bounded multi-agent orchestration skeleton for Phase 4 (`P4-C01`): split a complex objective into a DAG of agent-owned nodes, execute roots first, and unlock dependent nodes only after dependency success.

## Graph Model
- `graph_id`: `sup-<uuid>`
- `user_id`: owner scope for all child node runs
- `objective`: human-readable mission intent
- `status`: `planned | running | succeeded | failed | canceled`
- `nodes[]`:
  - `node_id`
  - `agent_id`
  - `message` (prompt for child run)
  - `depends_on[]` (DAG edges)
  - optional `max_attempts`, `budget`
  - runtime fields: `status`, `run_id`, `run_status`, `attempts`, `last_error`

Node statuses:
- `planned` -> waiting for dependencies
- `queued|running` -> child run started
- `succeeded` -> child run finished successfully
- `failed|canceled` -> child run terminal failure/cancel
- `blocked` -> dependency failure prevents execution

## API Surface
- `GET /supervisor/graphs/contract`
- `POST /supervisor/graphs/create`
- `GET /supervisor/graphs`
- `GET /supervisor/graphs/{graph_id}`
- `POST /supervisor/graphs/{graph_id}/launch`
- `POST /supervisor/graphs/{graph_id}/tick`

All endpoints are auth-scoped. Graph ownership is enforced (`user|admin` scopes), and each referenced `agent_id` must belong to the same effective `user_id`.

## Execution Semantics
- `create` validates:
  - non-empty node list
  - unique node ids
  - dependency references exist
  - no cycles
- `launch`:
  - moves graph to `running`
  - starts root nodes (dependencies already satisfied)
- `tick`:
  - refreshes child run statuses
  - marks downstream nodes `blocked` if dependency failed/canceled
  - starts newly-ready nodes
  - updates graph terminal status when complete

## Checkpoint + Resume (P4-C02 Slice)
- Graph state is persisted into SQLite table `supervisor_graphs` on each create/launch/tick checkpoint.
- Persisted payload includes full graph JSON plus indexed columns (`status`, `user_id`, `updated_at`, `checkpoint_count`).
- On runtime startup, `SupervisorTaskGraphManager` hydrates recent persisted graphs into memory automatically.
- Resume policy:
  - existing `run_id` links are preserved,
  - next `tick` reconciles node status from current child-run status,
  - dependency unlocking/blocking continues from hydrated state without rebuilding graph topology.

Current mode remains explicit operator control (`launch/tick`), but now with crash/restart recovery baseline for the supervisor layer.
