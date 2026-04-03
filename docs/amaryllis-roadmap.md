# Amaryllis Local Cognitive Platform Roadmap

## Mission
Build a local-first, voice-native, autonomous assistant for developer workflows that can evolve into a general personal cognitive system.

## Product Principles
- Local-first by default: core capabilities must work without cloud dependency.
- Linux-first runtime: Linux is the primary target platform; macOS is maintained as staging/early-adopter platform.
- Human trust before full autonomy: autonomy is delivered by strict policy levels, auditability, and reversible actions.
- Modular cognitive kernel: model backend can be replaced without rewriting orchestration.
- OSS ecosystem: core runtime, evals, and interfaces stay open and extensible.

## Autonomy Ladder
- L0: answer and advise.
- L1: produce plans and ask for explicit confirmation.
- L2: execute low-risk actions automatically.
- L3: execute medium-risk actions with scoped confirmation policy.
- L4: run autonomous background missions under budgets and guardrails.
- L5: full bounded autonomy with policy packs, kill switch, and audit trace.

## Execution Phases

### Phase 0 - Foundation Hardening (completed)
- Goal: make architecture and delivery process Tier-1 ready before expanding feature surface.
- Outcomes:
  - modular boundaries and RFC set for cognitive kernel/action layer/memory.
  - deterministic build/release chain.
  - baseline golden-task eval harness.
  - removal of known platform lifecycle risks.

### Phase 1 - Developer Amaryllis Alpha (completed)
- Goal: high-value daily workflows for developers on local runtime.
- Outcomes:
  - voice push-to-talk + visual execution HUD.
  - terminal/filesystem/browser/IDE action layer v1.
  - reliable async mission mode with replay and diagnostics.

### Phase 2 - Autonomous Operator Beta (completed)
- Goal: move from assistant to proactive operator with Tier-1 engineering guarantees.
- Outcomes:
  - modular cognitive kernel and backend-swappable cognition adapters.
  - deterministic reproducibility chain (toolchain/profile/provenance).
  - non-functional quality gates (performance, SLO burn-rate, reliability under faults/load).
  - wake-word and low-latency dialog loop.
  - richer OS integrations (calendar/mail/notifications/window-control).
  - L2-L3 autonomy with user-facing trust controls.

### Phase 3 - Amaryllis 1.0 OSS (completed)
- Goal: open, extensible, local cognitive platform.
- Outcomes:
  - skills/agents ecosystem and plugin compatibility contracts.
  - L4 bounded autonomous missions.
  - public benchmark + eval dashboards.

### Phase 4 - Amaryllis Personal Operator (completed)
- Goal: deliver an everyday local "Amaryllis on PC" experience with safe autonomy and desktop action depth.
- Outcomes:
  - unified multimodal user flow: intent -> plan -> execute -> explain -> iterate.
  - Linux-first desktop integrations with policy-gated capabilities and rollback hints.
  - bounded multi-agent supervision with mission checkpoints and resume.
  - release/nightly end-to-end journey KPIs and hardened distribution/update path.
  - backend-portable generation-loop contract (CPU/GPU/NPU parity + deterministic fallback semantics).
  - long-context reliability envelope (KV telemetry, cache policies, and quality-preserving memory pressure behavior).
  - offline trust stack: provenance-first answers, RAG threat-model coverage, and injection-resistant tool execution.
  - secure model supply-chain and quantization passport (artifact signatures, hashes, reproducible quant recipe metadata).
  - runtime environment passport and license admission gate for model/adapter/index artifacts.

### Phase 5 - Autonomous Reliability Shell (completed)
- Goal: make autonomy operations incident-resilient and operator-safe under real production failure modes.
- Outcomes:
  - global autonomy circuit breaker with service-level arm/disarm API and signed action audit trail.
  - deterministic emergency sequence: arm breaker -> optional kill switch -> recover -> disarm.
  - blocking release/nightly contract gate for emergency brake behavior.
  - blocking release/nightly breaker stability soak gate (multi-cycle drill cadence + scope parity + cycle-latency SLOs).
  - phase backlog and sprint structure for scoped breakers and persistence/restart semantics.

Execution-ready breakdown:
- `docs/amaryllis-phase5-backlog.md`

### Phase 6 - Cross-Domain Autonomy Hardening (completed)
- Goal: make emergency autonomy controls consistent across run-dispatch domains and remove false escalation noise during incident containment.
- Outcomes:
  - breaker-aware automation dispatch semantics (`run_blocked_autonomy_circuit_breaker`) without scheduler failure escalation drift.
  - cross-domain admission parity for `runs`, `automations`, and `supervisor` breaker scopes.
  - cross-domain operator diagnostics surface (`/service/runs/autonomy-circuit-breaker/domains`) with unified impact counters for `runs/automations/supervisor`.
  - autonomous high-risk tool-action boundary policy (`action_class` contract + breaker enforcement).
  - release/nightly contract expansion for cross-domain autonomy hardening, including mission report pack KPI contract enforcement for breaker gate results.

Execution-ready breakdown:
- `docs/amaryllis-phase6-backlog.md`

### Phase 7 - Autonomous News Missions (planned)
- Goal: productize autonomous, source-grounded daily intelligence missions (starting with AI news) with secure provider session and entitlement controls.
- Outcomes:
  - connector-based source ingestion (`reddit`, `x`, `web`) with normalized schema, dedup, and policy-aware retries.
  - mission-specific planning and automation lane for daily digest workflows.
  - grounded digest composer with explicit source citations and confidence markers.
  - provider session and entitlement contract for user-scoped cloud access with revocation/audit boundaries.
  - release/nightly blocking gates for news mission E2E reliability and provider-session security posture.

Execution-ready breakdown:
- `docs/amaryllis-phase7-backlog.md`
- `contracts/news_mission_v1.json`

### Parallel Track - Post-LLM Cognitive R&D
- Goal: keep backend swappable for next-gen local cognition.
- Outcomes:
  - stable cognitive backend interface.
  - experiment lane for memory-native and neuro-symbolic variants.
  - measurable improvement gates vs baseline backend.

## Research Delta (March 2026)
Derived from `deep-research-report-2` and integrated into execution priorities.

Execution-ready breakdown:
- `docs/amaryllis-phase4-execution-plan.md`

### 6-12 Month Priorities
- Treat generation-loop portability as a first-class contract, not an implementation detail.
- Optimize for sustained QoS (`TTFT`, decode stability, thermal/energy behavior), not peak tokens/s.
- Make RAG and tool execution zero-trust by default (provenance, allow-lists, strict sandbox boundaries).
- Standardize model artifact trust: signatures, checksums, reproducible quantization metadata.
- Gate model onboarding by license constraints and reproducibility requirements.

### 12-36 Month Priorities
- Move from workaround-heavy KV management toward architectures less sensitive to linear KV growth.
- Develop edge-native multimodal scheduling (stage-aware CPU/GPU/NPU placement) with stable UX under load.
- Introduce privacy-aware personalization lanes (adapter composition first, DP-aware paths as advanced tier).
- Evolve benchmark strategy from single-task metrics to end-to-end user-flow resilience and safety under distribution shift.

## Adoption Delta (March 2026)
Derived from `deep-research-report-3` (practical track, product/distribution focused) and integrated into Phase 4 mass-adoption lane.

Execution-ready breakdown:
- `docs/amaryllis-phase4-backlog.md` (Epic H)
- `docs/amaryllis-phase4-execution-plan.md` (WP-11..WP-16)

### 3-9 Month Priorities
- Optimize for install-to-first-value experience: hardware autodetect + ready profiles + model packages.
- Ship mainstream discovery/distribution channels (GitHub Releases + WinGet + Homebrew + Flathub).
- Make privacy/offline behavior explicit and user-visible (offline indicator + network intent disclosure).
- Productize developer path: OpenAI-compatible local API quickstarts and integration samples.
- Establish RU/EN localization and contributor governance baseline for sustainable OSS growth.

### 9-24 Month Priorities
- Evolve from feature delivery to adoption flywheel: activation, retention, and ecosystem plugin growth loops.
- Add partner-ready channel artifacts and reproducible distribution posture for OEM/self-hosted integrators.
- Maintain trust as scale grows: opt-in telemetry discipline, clear legal/licensing boundaries, and public KPI transparency.

## North-Star Metrics
- Golden task completion rate.
- Time-to-first-useful-action.
- Voice round-trip latency.
- Autonomous mission success rate.
- Unsafe action rate (must trend to near-zero).
- Recovery success rate after interruption/failure.
- Time-to-first-token (`TTFT`) and sustained decode stability under thermal pressure.
- Provenance coverage rate (answers with verifiable evidence trail).
- Prompt-injection containment rate for RAG/tool chains.
- Reproducibility pass rate across hardware/runtime profiles.
