# Amaryllis Phase 4 Execution Plan (Implementation Ready)

## Objective
Turn Phase 4 backlog into a start-now execution program with clear sequencing, dependencies, ownership boundaries, and blocking acceptance gates.

## Planning Horizon
- Window: 8 weeks
- Sprint cadence: 2 weeks
- Platform priority: Linux primary, macOS staging
- Model strategy: backend-swappable, no lock-in to a single provider/runtime

## Scope Baseline
This execution plan operationalizes Phase 4 tasks in:
- `docs/amaryllis-phase4-backlog.md`
- `docs/amaryllis-roadmap.md`

Research integration focus:
- generation-loop portability across CPU/GPU/NPU
- KV and QoS stability under long-context and thermal pressure
- provenance-first RAG and zero-trust tool execution
- secure model supply chain, reproducibility, and license admission

Mass-adoption integration focus:
- zero-friction first-run onboarding and model package UX
- privacy/offline transparency and trust-preserving defaults
- mainstream desktop distribution channels (WinGet/Homebrew/Flathub)
- developer adoption path (OpenAI-compatible local API + quickstarts)
- localization/governance discipline and KPI-driven growth loop

## Work Packages (WP)

| WP | Backlog IDs | Priority | Goal | Primary Code Areas | Blocking Gate |
|---|---|---|---|---|---|
| WP-01 | P4-E01 | P0 | Define generation-loop contract and backend conformance matrix | `models/model_manager.py`, `models/routing.py`, `api/model_api.py`, `runtime/config.py` | contract tests + conformance report |
| WP-02 | P4-E02 | P0 | Add KV pressure telemetry and policy transitions | `api/chat_api.py`, `runtime/observability.py`, `runtime/qos_governor.py`, `scripts/release/` | no silent degradation under pressure tests |
| WP-03 | P4-E03 | P0 | Implement QoS governor with deterministic mode switching (`quality/balanced/power_save`) | `runtime/config.py`, `runtime/server.py`, `scripts/release/perf_smoke_gate.py`, `scripts/release/user_journey_benchmark.py` | KPI gate on TTFT and stability |
| WP-04 | P4-E04 | P1 | Long-context reliability eval pack and release/nightly blocking | `eval/`, `scripts/release/`, `tests/test_user_journey_benchmark.py` | release/nightly block on long-context regressions |
| WP-05 | P4-F01 | P0 | Provenance required for RAG-grounded outputs | `memory/memory_manager.py`, `api/chat_api.py`, `api/memory_api.py`, `runtime/observability.py` | provenance coverage gate |
| WP-06 | P4-F02 + P4-F03 | P0 | Zero-trust tool execution and injection containment regression suite | `tools/tool_executor.py`, `tools/policy.py`, `tools/sandbox_runner.py`, `scripts/release/` | containment score and sandbox policy gate |
| WP-07 | P4-F04 | P0 | Secure model package and quant passport validator | `scripts/release/generate_release_provenance.py`, `api/model_api.py`, `models/model_manager.py` | artifact admission fails without signatures/metadata |
| WP-08 | P4-G01 | P1 | Runtime environment passport in release/nightly artifacts | `scripts/release/build_quality_dashboard_snapshot.py`, `scripts/release/publish_release_quality_snapshot.py`, `runtime/profile_loader.py` | every release artifact has env passport |
| WP-09 | P4-G02 | P1 | License admission policy for model/adapter/index onboarding | `api/model_api.py`, `runtime/compliance.py`, `policies/` | onboarding blocked on incompatible license policy |
| WP-10 | P4-G03 | P2 | Adapter-based personalization path with rollback/signature checks | `memory/`, `models/`, `storage/`, `api/` | reversible adapter stack with tests |
| WP-11 | P4-H01 + P4-H02 | P1 | First-run activation and model package UX | `api/model_api.py`, `models/model_manager.py`, `runtime/config.py`, `docs/` | first-run profile recommendation and package-based model install flow pass activation checks |
| WP-12 | P4-H03 | P1 | Offline/privacy transparency contract | `runtime/server.py`, `runtime/observability.py`, `api/`, `docs/` | offline indicator and network intent surface are testable and policy-consistent |
| WP-13 | P4-H04 | P1 | Mass distribution channel pipeline | `.github/workflows/`, `scripts/release/`, `docs/release-playbook.md` | release artifacts and manifests are publish-ready for WinGet/Homebrew/Flathub |
| WP-14 | P4-H05 | P1 | Developer adoption starter pack | `api/`, `sdk/`, `examples/`, `docs/` | local OpenAI-compatible quickstart completes in <15 minutes with contract tests |
| WP-15 | P4-H06 + P4-H07 | P2 | RU/EN localization and OSS governance package | `docs/`, `policies/`, `.github/` | localization baseline and contributor/legal governance docs are release-gated |
| WP-16 | P4-H08 | P1 | Adoption KPI funnel and growth dashboards | `runtime/observability.py`, `scripts/release/`, `observability/` | install/activation/retention/adoption KPI contracts publishable without breaking privacy policy |

## Completion Snapshot (Latest)

`WP-01` (`P4-E01`) is closed as implemented and release/nightly-gated.

Evidence:
- generation-loop portability contract endpoint is implemented and documented (`/models/generation-loop/contract`, `docs/generation-loop-contract.md`);
- provider conformance matrix includes contract versioning and provider-level pass/warn status surface;
- release/nightly workflows run blocking `generation_loop_conformance_gate.py` with machine-readable reports;
- conformance gate tests plus runtime contract tests (`tests/test_generation_loop_conformance_gate.py`, `tests/test_cognition_backend_runtime.py`) validate portability baseline behavior.

`WP-02` (`P4-E02`) is closed as implemented and release/nightly-gated.

Evidence:
- generation loop telemetry now emits KV pressure payload (`generation_loop_metrics.kv_cache`) with `pressure_state`, `estimated_tokens`, `estimated_bytes`, and `eviction_count` fields;
- QoS transition path is validated against real pressure telemetry (high/critical) instead of static `unknown` cache signals;
- release/nightly workflows run blocking `kv_pressure_policy_gate.py` with machine-readable reports;
- gate tests (`tests/test_kv_pressure_policy_gate.py`) validate pass/fail behavior and report contract.

`WP-03` (`P4-E03`) is closed as implemented and release/nightly-gated.

Evidence:
- QoS governor deterministic mode switching (`quality/balanced/power_save`) is enforced via runtime API and thermal/pressure transition logic;
- release/nightly workflows run blocking `qos_governor_gate.py` and `qos_mode_envelope_gate.py`;
- QoS mode envelope benchmark validates user-journey KPI thresholds per mode with contract checks on `active_mode/route_mode/auto_enabled`;
- gate tests (`tests/test_qos_governor_gate.py`, `tests/test_qos_mode_envelope_gate.py`) validate pass/fail behavior and report contracts.

`WP-05` (`P4-F01`) is closed as implemented and release/nightly-gated.

Evidence:
- chat responses expose provenance payload contract by default (`provenance_v1`) for non-stream and stream responses;
- grounded-response source trace coverage is enforced with explicit source-field checks;
- release/nightly workflows run blocking `provenance_coverage_gate.py` with machine-readable reports;
- gate and runtime tests (`tests/test_provenance_coverage_gate.py`, `tests/test_cognition_backend_runtime.py`) validate grounded provenance behavior and telemetry fields.

`WP-06` (`P4-F02` + `P4-F03`) is closed as implemented and release/nightly-gated.

Evidence:
- tool isolation policy enforces expanded unsafe-deserialization denylist coverage (`pickle`, `cloudpickle`, `pandas.read_pickle`, YAML unsafe loaders/tags) with deterministic policy-deny reasons;
- injection containment suite includes RAG payload injection and unsafe-deserialization attack scenarios with explicit required-scenario enforcement;
- release/nightly workflows run blocking `injection_containment_gate.py` with required scenarios and machine-readable reports;
- policy and gate tests (`tests/test_tool_isolation_policy.py`, `tests/test_injection_containment_gate.py`) validate pass/fail behavior and contract semantics.

`WP-08` (`P4-G01`) is closed as implemented and release/nightly-gated.

Evidence:
- `environment_passport_gate.py` builds runtime environment passport artifacts (host/runtime/toolchain/dependency lock/quantization/drivers) and enforces completeness thresholds;
- release/nightly workflows run blocking environment passport checks and publish machine-readable passport reports;
- quality dashboard snapshot ingests environment passport signals (`completeness_score_pct`, missing-required-fields);
- gate tests (`tests/test_environment_passport_gate.py`) validate pass/fail behavior and quantization-reference integration.

`WP-09` (`P4-G02`) is closed as implemented and release/nightly-gated.

Evidence:
- license admission policy engine is enforced for model package onboarding and dedicated license-admission API/manager path;
- release/nightly workflows run blocking `license_admission_gate.py` with required scenarios for allow/deny/non-commercial cases;
- quality dashboard snapshot ingests license admission signals (`admission_score_pct`, failed_scenarios);
- gate tests (`tests/test_license_admission_gate.py`, `tests/test_model_artifact_admission_gate.py`) validate policy regression behavior.

`WP-10` (`P4-G03`) is closed as implemented and release/nightly-gated.

Evidence:
- personalization adapter runtime contract is implemented via `/models/personalization/*` API endpoints for register/list/activate/rollback flows;
- adapter admission requires signed manifests (`hmac-sha256`) with managed trust enforcement under production security profile;
- adapter stack activation is scope-bounded (`user_id + base_package_id`) with deterministic rollback to previous active adapter;
- release/nightly workflows run blocking `personalization_adapter_gate.py` with machine-readable reports;
- API/gate tests (`tests/test_model_personalization_api.py`, `tests/test_personalization_adapter_gate.py`) validate reversible stack behavior and signature rejection paths.

`WP-16` (`P4-H08`) is closed as implemented and release/nightly-gated.

Evidence:
- blocking release/nightly chain is live: `adoption_kpi_schema_gate.py` -> `build_adoption_kpi_snapshot.py` -> `adoption_kpi_trend_gate.py` -> `publish_adoption_kpi_snapshot.py`;
- adoption trend output is integrated into mission KPI pack (`build_mission_success_recovery_report.py`) with `adoption_growth` class signals;
- runtime observability exports adoption snapshot and nightly adoption trend metrics in Prometheus (`runtime/observability.py`);
- dashboard/alert surface includes adoption trend guardrail (`observability/grafana/dashboard-amaryllis.json`, `observability/alerts/prometheus-rules.yml`);
- release and nightly workflows run this chain as blocking steps (`.github/workflows/release-gate.yml`, `.github/workflows/nightly-reliability.yml`);
- contract tests covering schema/snapshot/trend/publish/mission/observability pass in CI-local runs.

`WP-13` (`P4-H04`) is closed as implemented and release/nightly-gated.

Evidence:
- release/nightly workflows run blocking channel template gate + render + render gate (`distribution_channel_manifest_gate.py` -> `render_distribution_channel_manifests.py` -> `distribution_channel_render_gate.py`);
- rendered WinGet/Homebrew/Flathub manifests are exported as CI artifacts for operator handoff;
- channel documentation and release playbook include deterministic render and validation commands;
- channel contract tests cover template gate, render, and render-gate behavior.

`WP-11` (`P4-H01` + `P4-H02`) is closed as implemented and release/nightly-gated.

Evidence:
- onboarding and package-catalog runtime contracts are exposed via `/models/onboarding/*` and `/models/packages*` APIs;
- release/nightly workflows run blocking `first_run_activation_gate.py` with machine-readable reports;
- docs and playbook include first-run activation gate and contract references;
- API/unit contract tests cover onboarding profile, activation-plan/activate, package catalog, install, and license-admission flows.

`WP-15` (`P4-H06` + `P4-H07`) is closed as implemented and release/nightly-gated.

Evidence:
- governance baseline is present (`CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `GOVERNANCE.md`, `MAINTAINERS.md`, `TRADEMARK_POLICY.md`, `DCO.md`, `.github/PULL_REQUEST_TEMPLATE.md`);
- RU/EN localization baseline docs and starter templates are present under `docs/localization/ru/*` and `docs/localization/en/*`;
- release/nightly workflows run blocking `localization_governance_gate.py` and publish machine-readable reports;
- gate tests validate pass/fail contract behavior for localization/governance package.

Epic A baseline (`P4-A01` + `P4-A02` + `P4-A03`) is closed as implemented and release/nightly-gated.

Evidence:
- unified session lifecycle contract is implemented via `flow/session_manager.py` and `/flow/sessions/*` API endpoints (`api/flow_api.py`) with dedicated docs (`docs/flow-session-contract.md`);
- explicit plan-vs-execute interaction contract is implemented via `/agents/runs/interaction-modes` + `/agents/{agent_id}/runs/dispatch` (`api/agent_api.py`) with dedicated docs (`docs/agent-run-interaction-modes.md`);
- action timeline stream + explainability payload is implemented via `/agents/runs/{run_id}/events` + `/agents/runs/{run_id}/explain` with dedicated docs (`docs/mission-audit-timeline.md`, `docs/agent-run-explainability-feed.md`);
- release/nightly workflows run blocking `flow_interaction_gate.py` and `action_explainability_gate.py` and publish machine-readable reports;
- API/unit/gate tests cover flow session lifecycle, interaction-mode behavior, explainability feed payload semantics, and gate pass/fail behavior.

Epic B baseline (`P4-B01` + `P4-B02` + `P4-B03`) is closed as implemented and release/nightly-gated.

Evidence:
- Linux desktop integration pack is active via `desktop_action` tool and `LinuxDesktopActionAdapter` for notifications/clipboard/app-launch/window controls with policy guardrails;
- macOS staging parity surface is implemented via `MacOSDesktopActionAdapter` and parity smoke coverage;
- desktop action rollback metadata is deterministic for risky actions (`metadata.rollback_hint`, `metadata.mutating`) and is persisted in terminal action receipts;
- release/nightly workflows run blocking `desktop_action_rollback_gate.py` with machine-readable reports;
- adapter/runtime/gate tests cover Linux/macOS behavior, permission boundary for mutating actions, and rollback hint contract.

Epic C baseline (`P4-C01` + `P4-C02` + `P4-C03`) is closed as implemented and release/nightly-gated.

Evidence:
- bounded supervisor task-graph orchestration is implemented (`SupervisorTaskGraphManager`) with create/launch/tick execution semantics and ownership checks;
- runtime API contract is implemented and documented (`/supervisor/graphs/contract`, `create`, `list/get`, `launch`, `tick`, `verify`);
- checkpoint + resume semantics are persisted in SQLite (`supervisor_graphs`) and auto-hydrated on runtime startup;
- objective verification policy is enforced (`auto/manual`, keyword/response checks, explicit `/verify` override path);
- release/nightly workflows run blocking `supervisor_mission_gate.py` with machine-readable reports;
- manager/API/gate tests cover checkpoint recovery, manual/auto objective verification outcomes, and auth boundary behavior.

Epic D baseline (`P4-D01` + `P4-D02` + `P4-D03`) is closed as implemented and release/nightly-gated.

Evidence:
- end-to-end user journey benchmark is wired as strict release/nightly gate with stable baseline and KPI regression thresholds;
- mission success/recovery KPI pack v2 is built with class-level breakdown (`mission_execution`, `recovery`, `quality`, `runtime_qos`, `distribution`, `desktop_staging`, `user_flow`, `adoption_growth`, `nightly_reliability`);
- mission KPI pack now has explicit schema/completeness blocking gate (`mission_report_pack_gate.py`) in release/nightly workflows;
- distribution resilience path remains release-blocking and integrated into KPI pack inputs.

## Critical Path
1. `WP-01` -> baseline contract required before portability and QoS enforcement.
2. `WP-02` -> required before `WP-03` mode switching can be policy driven.
3. `WP-03` -> required before `WP-04` long-context gate can enforce stable SLOs.
4. `WP-05` + `WP-06` -> required before safety profile can be called Tier-1.
5. `WP-07` -> required before `WP-08` and `WP-09` can be end-to-end enforceable.
6. `WP-10` starts after `WP-07` and `WP-09` to avoid unsafe personalization pipeline.
7. `WP-11`..`WP-16` run as parallel mass-adoption lane after `WP-08` baseline evidence is live.

## Sprint Plan (Execution Sequence)

### Sprint P4-S1 (Weeks 1-2) - Contracts and Baselines
Goal: establish enforceable contracts and observability foundations.

In-sprint scope:
- `WP-01` generation-loop contract draft + conformance matrix skeleton
- `WP-02` KV telemetry schema + initial pressure state machine
- `WP-05` provenance payload contract for RAG responses
- `WP-06` unsafe deserialization deny rules in tool path
- `WP-07` quant passport schema draft and validator CLI stub
- `WP-08` environment passport schema and artifact placeholder wiring
- `WP-09` license policy schema draft

Definition of done:
- New/updated contracts are versioned and documented in `docs/`.
- CI has non-blocking reports for conformance/provenance/env-passport (warning mode allowed in S1).
- No behavior regressions in existing flow/supervisor/tool sandbox tests.

### Sprint P4-S2 (Weeks 3-4) - Enforcement and Blocking Gates
Goal: move from schema-level readiness to policy enforcement.

In-sprint scope:
- `WP-03` QoS governor mode switching + thresholds wired to runtime profile
- `WP-04` long-context reliability eval pack with baseline artifacts
- `WP-05` provenance mandatory for RAG-grounded answer class
- `WP-06` injection-resilience suite in release/nightly
- `WP-07` secure model package admission checks in model onboarding path
- `WP-08` environment passport published in release/nightly quality snapshot
- `WP-09` license admission checks in onboarding pipeline

Definition of done:
- Release/nightly fail on critical regressions (no warning-only mode for P0 gates).
- Quality dashboard includes TTFT/stability/provenance/containment/admission metrics.
- Artifact onboarding rejects unsigned or non-compliant model packages.

### Sprint P4-S3 (Weeks 5-6) - Hardening and Platform Parity
Goal: reliability hardening and Linux/mac staging parity verification.

In-sprint scope:
- harden false-positive/false-negative behavior of injection and license gates
- tune QoS governor thresholds using nightly trend data
- extend long-context eval scenarios and recovery assertions
- run Linux primary and mac staging parity smoke for new gate set

Definition of done:
- repeated nightlies pass with stable variance and no flaky gate behavior
- rollback path validated for failed model package admission
- operational playbook for incident triage documented

### Sprint P4-S4 (Weeks 7-8) - Personalization Lane
Goal: safe adapter-based personalization path.

In-sprint scope:
- `WP-10` adapter stack registry and rollback semantics
- signature and license compliance checks for adapter artifacts
- before/after personalization eval regression in local quality harness

Definition of done:
- base model remains immutable in default personalization path
- adapters are reversible and versioned with signed metadata
- personalization pipeline is policy-gated and observable

### Parallel Lane (Weeks 3-8) - Adoption and Distribution
Goal: convert technical readiness into mass user adoption and ecosystem growth.

In-lane scope:
- `WP-11` first-run onboarding profiles + package-based model install UX
- `WP-12` privacy/offline transparency contract and user-visible network intent
- `WP-13` channel pipeline hardening (WinGet/Homebrew/Flathub release readiness)
- `WP-14` developer quickstart and integration samples for OpenAI-compatible local API
- `WP-15` RU/EN localization and OSS governance baseline (license/trademark/DCO/CoC)
- `WP-16` adoption KPI funnel contract and dashboard publication path

Definition of done:
- install-to-first-answer journey is measurable and repeatable across channel builds
- users can verify offline behavior and network requirements from product UI/docs
- developer integration quickstart and API compatibility checks are release-gated
- growth KPIs are available in privacy-preserving mode (opt-in or local-only export)

## Start-Now PR Slices (First 10 Working Days)

| PR | Window | Scope | Suggested Files | Exit Check |
|---|---|---|---|---|
| PR-1 | Day 1-2 | generation-loop contract doc + conformance schema | `docs/`, `models/`, `api/model_api.py` | contract tests pass |
| PR-2 | Day 2-3 | KV telemetry event schema + observability plumbing | `runtime/observability.py`, `runtime/telemetry.py` | metrics emitted in test runtime |
| PR-3 | Day 3-4 | provenance response contract for RAG-grounded responses | `api/chat_api.py`, `memory/memory_manager.py` | provenance fields present in response payload |
| PR-4 | Day 4-5 | unsafe deserialization deny path in tool execution | `tools/tool_executor.py`, `tools/policy.py`, `tools/sandbox_runner.py` | security regression tests pass |
| PR-5 | Day 5-6 | quant passport schema + validator CLI stub | `scripts/release/`, `models/model_manager.py` | invalid passport rejected |
| PR-6 | Day 6-7 | environment passport artifact generation | `scripts/release/build_quality_dashboard_snapshot.py` | artifact emitted in quality snapshot |
| PR-7 | Day 7-8 | license admission schema + checker baseline | `runtime/compliance.py`, `api/model_api.py`, `policies/` | onboarding blocks incompatible license |
| PR-8 | Day 8-10 | QoS governor baseline mode transitions | `runtime/config.py`, `runtime/server.py`, `scripts/release/user_journey_benchmark.py` | TTFT/stability checks wired |

Adoption lane slices (next 10 working days after PR-1..PR-8):

| PR | Window | Scope | Suggested Files | Exit Check |
|---|---|---|---|---|
| PR-9 | Day 11-12 | first-run hardware profile recommendation and onboarding contract | `runtime/config.py`, `api/`, `docs/` | activation smoke (`install -> first response`) passes |
| PR-10 | Day 12-14 | model package catalog UX and install metadata contract | `models/model_manager.py`, `api/model_api.py`, `docs/` | package-based model selection/install tests pass |
| PR-11 | Day 14-16 | offline/privacy transparency surface + network intent report | `runtime/server.py`, `runtime/observability.py`, `docs/` | offline contract gate passes with explicit network declarations |
| PR-12 | Day 16-20 | distribution/developer adoption pack (channel manifests + quickstarts) | `.github/workflows/`, `scripts/release/`, `docs/`, `examples/` | channel manifest checks and API quickstart contract tests pass |

## Gate Matrix (Must Be Green Before Next Phase)

| Gate | Source | Type |
|---|---|---|
| user journey benchmark | `scripts/release/user_journey_benchmark.py` | blocking |
| perf smoke | `scripts/release/perf_smoke_gate.py` | blocking |
| QoS governor gate | `scripts/release/qos_governor_gate.py` | blocking |
| QoS mode envelope gate | `scripts/release/qos_mode_envelope_gate.py` | blocking |
| quality dashboard snapshot | `scripts/release/build_quality_dashboard_snapshot.py` | blocking |
| fault injection reliability | `scripts/release/fault_injection_reliability_gate.py` | blocking |
| mission queue load | `scripts/release/mission_queue_load_gate.py` | blocking |
| runtime lifecycle smoke | `scripts/release/runtime_lifecycle_smoke_gate.py` | blocking |
| Linux parity smoke | `scripts/release/linux_parity_smoke.py` | blocking |
| macOS desktop parity smoke | `scripts/release/macos_desktop_parity_smoke.py` | staging-blocking |
| distribution channel manifest readiness | `scripts/release/distribution_channel_manifest_gate.py` | blocking |
| distribution channel rendered-manifest gate | `scripts/release/distribution_channel_render_gate.py` | blocking |
| API quickstart compatibility gate | `scripts/release/api_quickstart_compatibility_gate.py` | blocking |
| first-run activation journey gate | `scripts/release/first_run_activation_gate.py` | blocking |
| localization/governance gate | `scripts/release/localization_governance_gate.py` | blocking |
| flow/interaction contract gate | `scripts/release/flow_interaction_gate.py` | blocking |
| action explainability gate | `scripts/release/action_explainability_gate.py` | blocking |
| desktop action rollback gate | `scripts/release/desktop_action_rollback_gate.py` | blocking |
| supervisor mission gate | `scripts/release/supervisor_mission_gate.py` | blocking |
| generation-loop conformance gate | `scripts/release/generation_loop_conformance_gate.py` | blocking |
| provenance coverage gate | `scripts/release/provenance_coverage_gate.py` | blocking |
| injection containment gate | `scripts/release/injection_containment_gate.py` | blocking |
| personalization adapter gate | `scripts/release/personalization_adapter_gate.py` | blocking |
| offline transparency gate | `scripts/release/offline_transparency_gate.py` | blocking |
| model artifact admission gate | `scripts/release/model_artifact_admission_gate.py` | blocking |
| license admission gate | `scripts/release/license_admission_gate.py` | blocking |
| environment passport completeness gate | `scripts/release/environment_passport_gate.py` | blocking |
| KV pressure policy gate | `scripts/release/kv_pressure_policy_gate.py` | blocking |
| adoption KPI schema gate | `scripts/release/adoption_kpi_schema_gate.py` | blocking |
| adoption KPI snapshot build/publish | `scripts/release/build_adoption_kpi_snapshot.py` + `scripts/release/publish_adoption_kpi_snapshot.py` | blocking |
| adoption KPI trend gate | `scripts/release/adoption_kpi_trend_gate.py` | blocking |
| mission report pack gate | `scripts/release/mission_report_pack_gate.py` | blocking |

Additional Phase 4 gates to add in this plan:
- none from the original list; all listed gates are now promoted to blocking in release/nightly pipelines.

## Ownership Boundaries

| Stream | Responsible Module Owner | Non-overlap Rule |
|---|---|---|
| runtime portability + QoS | `runtime/` + `models/` | no direct edits in tool sandbox policy without sync point |
| RAG provenance + memory reliability | `memory/` + `api/chat_api.py` | no mutation of tool permission schema |
| tool security + injection containment | `tools/` + `api/tool_api.py` | no edits to model routing logic |
| release gates + artifacts | `scripts/release/` + `eval/` | no runtime behavior change without dedicated PR |
| compliance/license/admission | `runtime/compliance.py` + `policies/` | no bypass path in API handlers |
| product onboarding + UX contract | `api/` + `docs/` + `runtime/config.py` | no hidden default changes without activation KPI impact note |
| growth/distribution | `.github/workflows/` + `scripts/release/` + `observability/` | channel automation changes must include rollback and manifest validation path |

## Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Gate flakiness from hardware variance | false blocks | profile-scoped thresholds + baseline trend deltas |
| Over-strict security policies reduce usability | user friction | policy levels with explicit fail-open only for non-prod dev mode |
| Provenance payload inflation increases latency | QoS regression | compact provenance schema + lazy expansion in UI |
| License metadata inconsistency across sources | onboarding instability | canonical license map + explicit unknown-license deny policy |
| Quant passport drift across converters | reproducibility loss | strict converter/version capture and hash attestation |
| Install friction across channels | adoption stall | channel-first packaging with install-to-first-answer KPI gate |
| Privacy messaging mismatch with runtime behavior | trust erosion | explicit offline/network intent contract and docs parity checks |
| Weak developer onboarding | low ecosystem growth | maintain OpenAI-compatible quickstart and integration test fixtures |

## Kickoff Checklist
1. Freeze P4-S1 scope to PR-1..PR-8 only.
2. Assign owners by stream and enforce non-overlap rules.
3. Enable daily artifact publishing for conformance/provenance/passport reports.
4. Review threshold values after first 3 nightly runs before making new gates blocking.
5. Hold weekly checkpoint: gate health, rollback drills, unresolved risk decisions.
6. Add adoption checkpoint: installs, activation, D7 retention, and feature adoption by channel.
7. Treat privacy/offline contract and distribution manifest checks as release-blocking once stable.
