# Amaryllis Phase 8 Backlog - Agent Factory

## Goal

Turn natural-language agent creation into a production-grade, contract-driven surface:
"сделай такого агента" -> deterministic plan -> idempotent apply.

## Milestones

| ID | Status | Task | Deliverables | Exit Signal |
| --- | --- | --- | --- | --- |
| P8-A01 | done | Extract factory core from API layer | `agents/factory.py` integrated in `api/agent_api.py` and `api/chat_api.py` | Quickstart inference is shared across API and chat paths |
| P8-A02 | done | Publish factory contract surface | `GET /agents/factory/contract` + `contracts/agent_factory_v1.json` | External client can discover stable plan/apply interface |
| P8-A03 | done | Add domain allowlist inference | `source_policy.mode=allowlist` + parsed `domains` from NL requests | Plan output shows scoped web domains before apply |
| P8-A04 | done | Validate in runtime tests | `tests/test_agent_factory.py`, updated `tests/test_cognition_backend_runtime.py` | CI proves contract endpoint and domain-aware planning behavior |
| P8-A05 | done | Add structured quickstart overrides | typed payload overrides (`kind/name/focus/tools/source_policy/automation`) with idempotency-safe fingerprinting | advanced callers can refine plan/apply output without losing NL entry flow |
| P8-A06 | in_progress | Product hardening and UX polish | richer intent profiles, safer source parsing fallback, explainable planning hints, docs/examples expansion | Factory handles broader request styles with consistent output |

## Next Priorities

1. Add conflict-resolution logic for mixed intents (for example coding + news in one request).
2. Extend eval fixtures for multilingual and noisy prompts to reduce parser drift regressions.
3. Add explainable planning hints (`inference_reason`) so UI can show why the factory chose a specific kind/source policy.
