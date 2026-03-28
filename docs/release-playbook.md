# Release and Rollback Playbook

## Release Gates

Mandatory gates before publish:

1. Security gate workflow (authz/security suite, SAST, dependency audit, SBOM)
2. Autonomy policy-pack contract gate (schema validation for L0-L5 rules)
3. Release provenance + SBOM artifact generation (signed provenance, dependency inventory, source artifact digest)
4. API compatibility gate
5. Canary smoke checks
6. Fault-injection reliability gate (provider/network/tool fault classes + retry/recovery assertions)
7. Injection containment gate (RAG/tool injection and unsafe-deserialization containment assertions)
8. Model artifact admission gate (signed package + quant passport + license policy assertions)
9. License admission gate (SPDX allow/deny policy regression and non-commercial/no-derivatives rejection checks)
10. Environment passport gate (runtime/toolchain/hardware/quant metadata completeness assertions)
11. Mission queue concurrency/load gate (queue-drain, p95 latency and success-rate SLO assertions)
12. User journey benchmark gate (intent -> planning -> execute -> review KPI assertions)
13. QoS governor gate (thermal-aware deterministic mode-switch contract assertions)
14. QoS mode envelope gate (journey KPI envelope for `quality/balanced/power_save` + runtime mode contract assertions)
15. Long-context reliability gate (regression block on relevance/stability/latency envelope)
16. Linux parity gate (runtime/voice/tools/observability API parity on Linux target)
17. Linux installer smoke gate (install/upgrade/channel rollback path on Linux target)
18. Distribution resilience report gate (aggregated parity + installer/rollback + runtime lifecycle blocking checks)
19. Distribution channel manifest readiness gate (WinGet/Homebrew/Flathub templates + placeholders)
20. Distribution channel rendered-manifest gate (rendered WinGet/Homebrew/Flathub outputs are publish-ready and verifiable)
21. API quickstart compatibility gate (OpenAI-compatible developer onboarding contract)
22. First-run activation gate (onboarding profile + activation plan + package catalog runtime contract)
23. Localization/governance gate (RU/EN docs + starter templates + contributor/legal governance package contract)
24. Flow/interaction gate (unified `/flow/sessions/*` + `/runs/dispatch` plan-vs-execute trust-boundary contract)
25. Action explainability gate (timeline stream + plain-language `reason/result/next_step` payload contract)
26. Autonomy circuit breaker gate (service emergency brake contract + execute-mode blocking assertions)
27. Autonomy circuit breaker stability soak gate (multi-cycle emergency drill cadence + scope parity + p95 cycle SLO assertions)
28. Desktop action rollback gate (Linux desktop action + rollback hint + terminal receipt contract)
29. Supervisor mission gate (bounded task-graph + checkpoint/resume + objective verification contract)
30. Generation-loop conformance gate (backend portability matrix + contract identity assertions)
31. Provenance coverage gate (grounded-response source-trace coverage + stream/telemetry provenance contract assertions)
32. KV pressure policy gate (generation-loop KV telemetry contract + pressure-driven QoS transition assertions)
33. Adoption KPI schema gate (install/activation/retention/feature-adoption contract assertions)
34. Adoption KPI snapshot build gate (publishable adoption artifact + summary score)
35. Adoption KPI trend regression gate (baseline-relative regression budget enforcement)
36. Release quality dashboard snapshot gate (final post-Linux benchmark/reliability artifact + trend deltas)
37. Mission success/recovery report pack gate (v2 schema + class/KPI completeness contract)
38. Mission success/recovery report pack export (public KPI artifact)
39. Disaster recovery gate (backup + verify + restore drill)
40. Compliance operations gate (access review + incidents + evidence export)

Staging companion (non-blocking):
- macOS desktop action parity smoke (`scripts/release/macos_desktop_parity_smoke.py`)

Channel template reference:
- `docs/distribution-channels.md`

Developer quickstart reference:
- `docs/developer-quickstart.md`

Adoption KPI schema gate reference:
- `docs/adoption-kpi-schema-gate.md`

Adoption KPI snapshot reference:
- `docs/adoption-kpi-snapshot.md`

First-run activation gate reference:
- `docs/first-run-activation-gate.md`

Localization/governance gate reference:
- `docs/localization-governance-gate.md`

Flow/interaction gate reference:
- `docs/flow-interaction-gate.md`

Action explainability gate reference:
- `docs/action-explainability-gate.md`

Autonomy circuit breaker gate reference:
- `docs/autonomy-circuit-breaker.md`

Desktop action rollback gate reference:
- `docs/desktop-action-rollback-gate.md`

Supervisor mission gate reference:
- `docs/supervisor-mission-gate.md`

Generation-loop conformance gate reference:
- `docs/generation-loop-conformance-gate.md`

Provenance coverage gate reference:
- `docs/provenance-coverage-gate.md`

Personalization adapter gate reference:
- `docs/personalization-adapter-gate.md`

KV pressure policy gate reference:
- `docs/kv-pressure-policy-gate.md`

QoS mode envelope gate reference:
- `docs/qos-mode-envelope-gate.md`

Mission report pack gate reference:
- `docs/mission-report-pack-gate.md`

Commands:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/release/check_autonomy_policy_pack.py
git archive --format=tar.gz HEAD -o artifacts/release-source.tar.gz
python scripts/release/generate_release_provenance.py --repo-root . --artifact artifacts/release-source.tar.gz
python scripts/release/api_compat_gate.py
python scripts/release/canary_smoke.py
python scripts/release/perf_smoke_gate.py --iterations 3 --max-p95-latency-ms 350 --max-error-rate-pct 0 --output artifacts/perf-smoke-report.json
python scripts/release/runtime_lifecycle_smoke_gate.py --max-startup-slo-latency-ms 3000 --output artifacts/runtime-lifecycle-smoke-report.json
python scripts/release/fault_injection_reliability_gate.py --retry-max-attempts 2 --scenario-timeout-sec 8 --min-pass-rate-pct 100
python scripts/release/injection_containment_gate.py \
  --min-containment-score-pct 100 \
  --max-failed-scenarios 0 \
  --require-scenario rag_embedded_tool_call_is_ignored \
  --require-scenario pickle_deserialization_blocked \
  --require-scenario cloudpickle_deserialization_blocked \
  --require-scenario pandas_read_pickle_blocked \
  --require-scenario yaml_python_tag_blocked \
  --output artifacts/injection-containment-report.json
python scripts/release/personalization_adapter_gate.py --min-registered-adapters 2 --output artifacts/personalization-adapter-gate-report.json
python scripts/release/model_artifact_admission_gate.py --min-admission-score-pct 100 --max-failed-scenarios 0 --require-scenario valid_manifest_admitted --require-scenario missing_quant_recipe_rejected --require-scenario denied_license_rejected --output artifacts/model-artifact-admission-report.json
python scripts/release/license_admission_gate.py --min-admission-score-pct 100 --max-failed-scenarios 0 --require-scenario allowed_license_admitted --require-scenario denied_spdx_rejected --require-scenario noncommercial_rejected --output artifacts/license-admission-report.json
python scripts/release/environment_passport_gate.py --model-artifact-admission-report artifacts/model-artifact-admission-report.json --min-completeness-score-pct 100 --max-missing-required 0 --output artifacts/environment-passport-report.json
python scripts/release/mission_queue_load_gate.py --runs-total 40 --submit-concurrency 8 --worker-count 4 --task-latency-ms 35 --scenario-timeout-sec 30 --min-success-rate-pct 99 --max-failed-runs 0 --max-p95-queue-wait-ms 1500 --max-p95-end-to-end-ms 5000
python scripts/release/user_journey_benchmark.py --iterations 5 --min-success-rate-pct 100 --max-p95-journey-latency-ms 3000 --max-p95-plan-dispatch-latency-ms 1200 --max-p95-execute-dispatch-latency-ms 1200 --min-plan-to-execute-conversion-rate-pct 100 --min-activation-success-rate-pct 100 --max-blocked-activation-rate-pct 0 --max-p95-activation-latency-ms 600000 --min-install-success-rate-pct 100 --min-retention-proxy-success-rate-pct 100 --min-feature-adoption-rate-pct 100 --baseline eval/baselines/quality/user_journey_benchmark_baseline.json --output artifacts/user-journey-benchmark-report.json --strict
python scripts/release/qos_governor_gate.py --initial-mode balanced --expect-critical-mode power_save --expect-final-mode quality --output artifacts/qos-governor-gate-report.json
python scripts/release/qos_mode_envelope_gate.py --journey-iterations 2 --max-p95-journey-latency-ms 3500 --max-p95-plan-dispatch-latency-ms 1500 --max-p95-execute-dispatch-latency-ms 1500 --max-p95-activation-latency-ms 600000 --max-failed-modes 0 --output artifacts/qos-mode-envelope-gate-report.json
python scripts/release/long_context_reliability_gate.py --dataset eval/datasets/quality/long_context_reliability_cases.json --iterations 2 --min-run-success-rate-pct 100 --min-relevance-score-pct 95 --min-stability-score-pct 100 --max-p95-latency-ms 4000 --baseline eval/baselines/quality/long_context_reliability_baseline.json --max-relevance-regression-pct 2 --max-stability-regression-pct 1 --max-latency-regression-pct 40 --output artifacts/long-context-reliability-report.json
python scripts/release/macos_desktop_parity_smoke.py --iterations 2 --output artifacts/macos-desktop-parity-smoke-report.json
python scripts/release/linux_parity_smoke.py --iterations 1 --require-linux --output artifacts/linux-parity-smoke-report.json
python scripts/release/linux_installer_smoke.py --require-linux --output artifacts/linux-installer-smoke-report.json
python scripts/release/build_distribution_resilience_report.py --linux-parity-report artifacts/linux-parity-smoke-report.json --linux-installer-report artifacts/linux-installer-smoke-report.json --runtime-lifecycle-report artifacts/runtime-lifecycle-smoke-report.json --output artifacts/distribution-resilience-report.json
python scripts/release/distribution_channel_manifest_gate.py --output artifacts/distribution-channel-manifest-report.json
python scripts/release/api_quickstart_compatibility_gate.py --output artifacts/api-quickstart-compat-report.json
python scripts/release/render_distribution_channel_manifests.py --version "<version>" --windows-x64-url "<url>" --windows-x64-sha256 "<sha256>" --macos-arm64-url "<url>" --macos-arm64-sha256 "<sha256>" --macos-x64-url "<url>" --macos-x64-sha256 "<sha256>" --flathub-archive-url "<url>" --flathub-archive-sha256 "<sha256>" --output-dir artifacts/distribution-channels-rendered --report artifacts/distribution-channels-rendered-report.json
python scripts/release/distribution_channel_render_gate.py --render-report artifacts/distribution-channels-rendered-report.json --expected-version "<version>" --output artifacts/distribution-channel-render-gate-report.json
python scripts/release/first_run_activation_gate.py --output artifacts/first-run-activation-gate-report.json
python scripts/release/localization_governance_gate.py --output artifacts/localization-governance-gate-report.json
python scripts/release/flow_interaction_gate.py --output artifacts/flow-interaction-gate-report.json
python scripts/release/action_explainability_gate.py --output artifacts/action-explainability-gate-report.json
python scripts/release/autonomy_circuit_breaker_gate.py --output artifacts/autonomy-circuit-breaker-gate-report.json
python scripts/release/autonomy_circuit_breaker_soak_gate.py --cycles 6 --min-success-rate-pct 100 --max-failed-cycles 0 --max-p95-cycle-latency-ms 4500 --output artifacts/autonomy-circuit-breaker-soak-gate-report.json
python scripts/release/desktop_action_rollback_gate.py --output artifacts/desktop-action-rollback-gate-report.json
python scripts/release/supervisor_mission_gate.py --output artifacts/supervisor-mission-gate-report.json
python scripts/release/generation_loop_conformance_gate.py --min-providers 1 --max-warning-providers 2 --output artifacts/generation-loop-conformance-gate-report.json
python scripts/release/provenance_coverage_gate.py --min-grounded-sources 1 --output artifacts/provenance-coverage-gate-report.json
python scripts/release/kv_pressure_policy_gate.py --min-pressure-events 1 --min-critical-events 1 --output artifacts/kv-pressure-policy-gate-report.json
python scripts/release/build_quality_dashboard_snapshot.py --perf-report artifacts/perf-smoke-report.json --fault-injection-report artifacts/fault-injection-reliability-report.json --injection-containment-report artifacts/injection-containment-report.json --model-artifact-admission-report artifacts/model-artifact-admission-report.json --license-admission-report artifacts/license-admission-report.json --environment-passport-report artifacts/environment-passport-report.json --mission-queue-report artifacts/mission-queue-load-report.json --runtime-lifecycle-report artifacts/runtime-lifecycle-smoke-report.json --user-journey-report artifacts/user-journey-benchmark-report.json --qos-governor-report artifacts/qos-governor-gate-report.json --long-context-report artifacts/long-context-reliability-report.json --distribution-resilience-report artifacts/distribution-resilience-report.json --distribution-channel-manifest-report artifacts/distribution-channel-manifest-report.json --api-quickstart-report artifacts/api-quickstart-compat-report.json --macos-desktop-parity-report artifacts/macos-desktop-parity-smoke-report.json --baseline eval/baselines/quality/release_quality_dashboard_baseline.json --output artifacts/release-quality-dashboard-final.json --trend-output artifacts/release-quality-dashboard-trend-final.json
python scripts/release/adoption_kpi_schema_gate.py --user-journey-report artifacts/user-journey-benchmark-report.json --api-quickstart-report artifacts/api-quickstart-compat-report.json --distribution-channel-manifest-report artifacts/distribution-channel-manifest-report.json --quality-dashboard-report artifacts/release-quality-dashboard-final.json --output artifacts/adoption-kpi-schema-gate-report.json
python scripts/release/build_adoption_kpi_snapshot.py --schema-gate-report artifacts/adoption-kpi-schema-gate-report.json --user-journey-report artifacts/user-journey-benchmark-report.json --api-quickstart-report artifacts/api-quickstart-compat-report.json --distribution-channel-manifest-report artifacts/distribution-channel-manifest-report.json --quality-dashboard-report artifacts/release-quality-dashboard-final.json --output artifacts/adoption-kpi-snapshot-final.json --release-id "<release_id>" --release-channel "<channel>" --commit-sha "<sha>"
python scripts/release/adoption_kpi_trend_gate.py --snapshot-report artifacts/adoption-kpi-snapshot-final.json --baseline eval/baselines/quality/adoption_kpi_snapshot_baseline.json --max-activation-success-regression-pct 1 --max-activation-blocked-rate-increase-pct 0 --max-install-success-regression-pct 1 --max-retention-proxy-regression-pct 1 --max-feature-adoption-regression-pct 2 --max-api-quickstart-pass-rate-regression-pct 1 --max-channel-coverage-regression-pct 1 --output artifacts/adoption-kpi-trend-gate-report.json
python scripts/release/publish_adoption_kpi_snapshot.py --snapshot-report artifacts/adoption-kpi-snapshot-final.json --channel release --install-root ~/.local/share/amaryllis
python scripts/release/publish_release_quality_snapshot.py --snapshot-report artifacts/release-quality-dashboard-final.json --trend-report artifacts/release-quality-dashboard-trend-final.json --install-root ~/.local/share/amaryllis
python scripts/release/build_mission_success_recovery_report.py --mission-queue-report artifacts/mission-queue-load-report.json --fault-injection-report artifacts/fault-injection-reliability-report.json --quality-dashboard-report artifacts/release-quality-dashboard-final.json --adoption-kpi-trend-report artifacts/adoption-kpi-trend-gate-report.json --distribution-resilience-report artifacts/distribution-resilience-report.json --macos-desktop-parity-report artifacts/macos-desktop-parity-smoke-report.json --user-journey-report artifacts/user-journey-benchmark-report.json --scope release --output artifacts/mission-success-recovery-report.json
python scripts/release/mission_report_pack_gate.py --report artifacts/mission-success-recovery-report.json --expected-scope release --output artifacts/mission-report-pack-gate-report.json
python scripts/release/disaster_recovery_gate.py
python scripts/release/compliance_ops_gate.py
```

## Canary Procedure

1. Build candidate from release branch/tag.
2. Run `scripts/release/canary_smoke.py` on candidate artifact.
3. Verify:
   - `/v1` routes respond
   - deprecation headers exist on legacy paths
   - observability endpoints are healthy

## Rollback Procedure

If canary or production checks fail:

1. Freeze new rollouts.
2. Execute:

```bash
scripts/release/rollback_local.sh <last_known_good_tag_or_commit>
```

For Linux channelized runtime rollback (installer-based deployment):

```bash
python3 scripts/release/linux_channel_rollback.py --channel canary --steps 1
```

3. Re-run smoke + compatibility + Linux parity/installer + distribution-resilience + disaster-recovery checks.
4. Post incident summary with:
   - failing gate
   - impacted version/tag
   - recovery timestamp
