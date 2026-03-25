# Jarvis Phase 4 Execution Plan (Implementation Ready)

## Objective
Turn Phase 4 backlog into a start-now execution program with clear sequencing, dependencies, ownership boundaries, and blocking acceptance gates.

## Planning Horizon
- Window: 8 weeks
- Sprint cadence: 2 weeks
- Platform priority: Linux primary, macOS staging
- Model strategy: backend-swappable, no lock-in to a single provider/runtime

## Scope Baseline
This execution plan operationalizes Phase 4 tasks in:
- `/Users/bogdan/Amaryllis/docs/jarvis-phase4-backlog.md`
- `/Users/bogdan/Amaryllis/docs/jarvis-roadmap.md`

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
| WP-02 | P4-E02 | P0 | Add KV pressure telemetry and policy transitions | `runtime/observability.py`, `runtime/telemetry.py`, `runtime/server.py`, `slo_profiles/` | no silent degradation under pressure tests |
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
| quality dashboard snapshot | `scripts/release/build_quality_dashboard_snapshot.py` | blocking |
| fault injection reliability | `scripts/release/fault_injection_reliability_gate.py` | blocking |
| mission queue load | `scripts/release/mission_queue_load_gate.py` | blocking |
| runtime lifecycle smoke | `scripts/release/runtime_lifecycle_smoke_gate.py` | blocking |
| Linux parity smoke | `scripts/release/linux_parity_smoke.py` | blocking |
| macOS desktop parity smoke | `scripts/release/macos_desktop_parity_smoke.py` | staging-blocking |
| adoption KPI schema gate | `scripts/release/adoption_kpi_schema_gate.py` | blocking |

Additional Phase 4 gates to add in this plan:
- generation-loop conformance gate
- provenance coverage gate
- injection containment regression gate
- model package + quant passport admission gate
- environment passport completeness gate
- license admission gate
- first-run activation journey gate
- offline transparency/network intent gate
- distribution channel manifest readiness gate
- API quickstart compatibility gate

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
