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
8. Model artifact admission gate (signed package + quant passport policy assertions)
9. Environment passport gate (runtime/toolchain/hardware/quant metadata completeness assertions)
10. Mission queue concurrency/load gate (queue-drain, p95 latency and success-rate SLO assertions)
11. User journey benchmark gate (intent -> planning -> execute -> review KPI assertions)
12. QoS governor gate (thermal-aware deterministic mode-switch contract assertions)
13. Linux parity gate (runtime/voice/tools/observability API parity on Linux target)
14. Linux installer smoke gate (install/upgrade/channel rollback path on Linux target)
15. Distribution resilience report gate (aggregated parity + installer/rollback + runtime lifecycle blocking checks)
16. Distribution channel manifest readiness gate (WinGet/Homebrew/Flathub templates + placeholders)
17. API quickstart compatibility gate (OpenAI-compatible developer onboarding contract)
18. Release quality dashboard snapshot gate (final post-Linux benchmark/reliability artifact + trend deltas)
19. Mission success/recovery report pack export (public KPI artifact)
20. Disaster recovery gate (backup + verify + restore drill)
21. Compliance operations gate (access review + incidents + evidence export)

Staging companion (non-blocking):
- macOS desktop action parity smoke (`scripts/release/macos_desktop_parity_smoke.py`)

Channel template reference:
- `docs/distribution-channels.md`

Developer quickstart reference:
- `docs/developer-quickstart.md`

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
python scripts/release/injection_containment_gate.py --min-containment-score-pct 100 --max-failed-scenarios 0 --output artifacts/injection-containment-report.json
python scripts/release/model_artifact_admission_gate.py --min-admission-score-pct 100 --max-failed-scenarios 0 --output artifacts/model-artifact-admission-report.json
python scripts/release/environment_passport_gate.py --model-artifact-admission-report artifacts/model-artifact-admission-report.json --min-completeness-score-pct 100 --max-missing-required 0 --output artifacts/environment-passport-report.json
python scripts/release/mission_queue_load_gate.py --runs-total 40 --submit-concurrency 8 --worker-count 4 --task-latency-ms 35 --scenario-timeout-sec 30 --min-success-rate-pct 99 --max-failed-runs 0 --max-p95-queue-wait-ms 1500 --max-p95-end-to-end-ms 5000
python scripts/release/user_journey_benchmark.py --iterations 5 --min-success-rate-pct 100 --max-p95-journey-latency-ms 3000 --max-p95-plan-dispatch-latency-ms 1200 --max-p95-execute-dispatch-latency-ms 1200 --min-plan-to-execute-conversion-rate-pct 100 --baseline eval/baselines/quality/user_journey_benchmark_baseline.json --output artifacts/user-journey-benchmark-report.json --strict
python scripts/release/qos_governor_gate.py --initial-mode balanced --expect-critical-mode power_save --expect-final-mode quality --output artifacts/qos-governor-gate-report.json
python scripts/release/macos_desktop_parity_smoke.py --iterations 2 --output artifacts/macos-desktop-parity-smoke-report.json
python scripts/release/linux_parity_smoke.py --iterations 1 --require-linux --output artifacts/linux-parity-smoke-report.json
python scripts/release/linux_installer_smoke.py --require-linux --output artifacts/linux-installer-smoke-report.json
python scripts/release/build_distribution_resilience_report.py --linux-parity-report artifacts/linux-parity-smoke-report.json --linux-installer-report artifacts/linux-installer-smoke-report.json --runtime-lifecycle-report artifacts/runtime-lifecycle-smoke-report.json --output artifacts/distribution-resilience-report.json
python scripts/release/distribution_channel_manifest_gate.py --output artifacts/distribution-channel-manifest-report.json
python scripts/release/api_quickstart_compatibility_gate.py --output artifacts/api-quickstart-compat-report.json
python scripts/release/render_distribution_channel_manifests.py --version "<version>" --windows-x64-url "<url>" --windows-x64-sha256 "<sha256>" --macos-arm64-url "<url>" --macos-arm64-sha256 "<sha256>" --macos-x64-url "<url>" --macos-x64-sha256 "<sha256>" --flathub-archive-url "<url>" --flathub-archive-sha256 "<sha256>" --output-dir artifacts/distribution-channels-rendered --report artifacts/distribution-channels-rendered-report.json
python scripts/release/build_quality_dashboard_snapshot.py --perf-report artifacts/perf-smoke-report.json --fault-injection-report artifacts/fault-injection-reliability-report.json --injection-containment-report artifacts/injection-containment-report.json --model-artifact-admission-report artifacts/model-artifact-admission-report.json --environment-passport-report artifacts/environment-passport-report.json --mission-queue-report artifacts/mission-queue-load-report.json --runtime-lifecycle-report artifacts/runtime-lifecycle-smoke-report.json --user-journey-report artifacts/user-journey-benchmark-report.json --distribution-resilience-report artifacts/distribution-resilience-report.json --api-quickstart-report artifacts/api-quickstart-compat-report.json --macos-desktop-parity-report artifacts/macos-desktop-parity-smoke-report.json --baseline eval/baselines/quality/release_quality_dashboard_baseline.json --output artifacts/release-quality-dashboard-final.json --trend-output artifacts/release-quality-dashboard-trend-final.json
python scripts/release/publish_release_quality_snapshot.py --snapshot-report artifacts/release-quality-dashboard-final.json --trend-report artifacts/release-quality-dashboard-trend-final.json --install-root ~/.local/share/amaryllis
python scripts/release/build_mission_success_recovery_report.py --mission-queue-report artifacts/mission-queue-load-report.json --fault-injection-report artifacts/fault-injection-reliability-report.json --quality-dashboard-report artifacts/release-quality-dashboard-final.json --distribution-resilience-report artifacts/distribution-resilience-report.json --macos-desktop-parity-report artifacts/macos-desktop-parity-smoke-report.json --user-journey-report artifacts/user-journey-benchmark-report.json --scope release --output artifacts/mission-success-recovery-report.json
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
