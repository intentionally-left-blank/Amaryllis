# ADR-0001: Cognitive Kernel Interface Contracts (v1)

- Status: Accepted
- Date: 2026-03-20
- Related backlog: `P2-A01` in `docs/amaryllis-phase2-backlog.md`

## Context

Phase 2 requires a modular cognitive kernel where orchestration can depend on stable interfaces instead of concrete runtime classes.

Current runtime behavior is stable, but architecture still relies mostly on direct concrete types (`Planner`, `TaskExecutor`, `MemoryManager`, `ToolRegistry`) across layers. This creates coupling risk for:
- backend-swappable cognition work,
- Linux-first productization,
- deterministic release hardening and compatibility policy.

## Decision

Introduce a versioned kernel contract surface in `kernel/contracts.py`:
- `PlannerContract`
- `ExecutorContract`
- `MemoryContract`
- `ToolRouterContract`
- shared alias `CheckpointWriter`
- version marker `KERNEL_CONTRACTS_VERSION = "kernel.contracts.v1"`

The contract intentionally reflects the currently used minimal API surface and does not force runtime behavior changes in this step.

## Scope (This ADR)

Included:
- formal protocol interfaces for planner/executor/memory/tool-router,
- versioned contract marker for compatibility tracking,
- contract package export via `kernel/__init__.py`.

Not included:
- moving orchestration into `kernel/orchestration` (planned in `P2-A02`),
- CI boundary rules for `kernel` (planned in `P2-A04`),
- backend cognition adapter implementation (planned in `P2-A03`).

## Consequences

Positive:
- clear seam for dependency inversion in upcoming refactors,
- explicit contract surface for adapter compatibility tests,
- reduced risk during future backend/planner/executor replacement.

Trade-offs:
- temporary dual world (contracts + concrete implementations) until `P2-A02` migration is complete,
- protocol drift must be controlled via contract versioning discipline.

## Contract Versioning Policy

- Backward-compatible additions: keep `kernel.contracts.v1`.
- Breaking changes: introduce a new major contract version (for example `v2`) and provide migration notes.
- Runtime and CI checks should eventually assert supported contract versions during startup and gates.
