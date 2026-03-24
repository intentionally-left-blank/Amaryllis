# Jarvis Local Cognitive Platform Roadmap

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

### Phase 1 - Developer Jarvis Alpha (completed)
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

### Phase 3 - Jarvis 1.0 OSS (completed)
- Goal: open, extensible, local cognitive platform.
- Outcomes:
  - skills/agents ecosystem and plugin compatibility contracts.
  - L4 bounded autonomous missions.
  - public benchmark + eval dashboards.

### Phase 4 - Jarvis Personal Operator (current)
- Goal: deliver an everyday local "Jarvis on PC" experience with safe autonomy and desktop action depth.
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

### Parallel Track - Post-LLM Cognitive R&D
- Goal: keep backend swappable for next-gen local cognition.
- Outcomes:
  - stable cognitive backend interface.
  - experiment lane for memory-native and neuro-symbolic variants.
  - measurable improvement gates vs baseline backend.

## Research Delta (March 2026)
Derived from `deep-research-report-2` and integrated into execution priorities.

Execution-ready breakdown:
- `/Users/bogdan/Amaryllis/docs/jarvis-phase4-execution-plan.md`

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
