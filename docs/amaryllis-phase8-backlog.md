# Amaryllis Phase 8 Backlog - Agent Factory

## Goal

Turn natural-language agent creation into a production-grade, contract-driven surface:
"сделай такого агента" -> deterministic plan -> idempotent apply.

## Phase Status

`completed` (all milestones and closure controls shipped)

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
| P8-A09 | done | Calibrate profile-specific latency envelopes | `eval/baselines/quality/agent_factory_plan_perf_envelope.json` + baseline-profile wiring in release/nightly workflows | Perf budgets are reproducible and versioned per profile (`release/nightly/dev_*`) |
| P8-A10 | done | Add baseline refresh workflow and drift report | `scripts/release/agent_factory_plan_perf_baseline_refresh.py` + scheduled `.github/workflows/agent-factory-baseline-refresh.yml` + refresh tests | Weekly drift snapshot produces suggested envelope updates without blocking release flow |
| P8-A11 | done | Expand multilingual schedule/timezone + UI-ready explainability | extended locale aliases in `agents/factory.py` + `inference_reason_view` in plan payload + contract/tests updates | Planner handles broader locale phrasing and frontend gets structured explanation payload |
| P8-A12 | done | Render quickstart explainability in macOS UI | `APIQuickstartInferenceReasonView` models + `AgentsView` explainability card (chips, confidence, conflict timeline, override tags) | User can inspect "why this plan" directly in desktop quickstart flow |
| P8-A13 | done | Add baseline refresh PR policy gate | `scripts/release/agent_factory_plan_perf_baseline_policy_gate.py` + `.github/workflows/agent-factory-baseline-policy-gate.yml` + gate tests/docs | Baseline drift over auto limits is blocked without manual-approval metadata |
| P8-A14 | done | Add timezone abbreviation disambiguation hints + extra LatAm locale coverage | `agents/factory.py` timezone ambiguity hints (`IST/CST`) + `disambiguation_hints` in `inference_reason_view` + `CDMX`/Portuguese schedule coverage in fixtures/tests | Planner remains deterministic but surfaces ambiguity explicitly for UI confirmation |

## Closure Addenda (April 2026)

1. `done` Auto-generated baseline update PR template now ships from refresh workflow:
   - `scripts/release/agent_factory_plan_perf_baseline_pr_template.py`
   - `artifacts/agent-factory-plan-perf-baseline-pr-template.md`
   - `artifacts/agent-factory-plan-perf-baseline-pr-template-metadata.json`
2. `done` Repo-level CI now validates baseline-refresh PR description metadata:
   - `scripts/release/agent_factory_plan_perf_baseline_pr_description_gate.py`
   - wired in `.github/workflows/agent-factory-baseline-policy-gate.yml`
3. `done` Timezone disambiguation hints now include locale-aware fallback suggestions in Agent Factory inference explainability.
