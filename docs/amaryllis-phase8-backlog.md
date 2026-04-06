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
| P8-A06 | done | Product hardening and UX polish | weighted mixed-intent conflict resolution + explainable `inference_reason` + resilient schedule/source parsing + expanded docs/examples | Factory handles broader request styles with deterministic, inspectable output |
| P8-A07 | done | Add deterministic intent-eval release/nightly gate | `eval/fixtures/agent_factory/*` + `scripts/release/agent_factory_intent_gate.py` + CI artifact wiring | Intent inference drift is caught as a blocking contract regression |
| P8-A08 | done | Add blocking quickstart plan perf gate | `scripts/release/agent_factory_plan_perf_gate.py` + unit tests + release/nightly artifact wiring | p95/error-rate regressions on `/v1/agents/quickstart/plan` are caught before release |

## Next Priorities

1. Extend schedule/timezone inference further for additional locales and ambiguous aliases beyond current RU/EN + common US/EU abbreviations.
2. Add UI-level visualization for `inference_reason` signals and conflict-resolution path.
3. Calibrate hardware/profile-specific latency budgets for `agent_factory_plan_perf_gate` and publish baseline envelopes.
