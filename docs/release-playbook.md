# Release and Rollback Playbook

## Release Gates

Mandatory gates before publish:

1. Security gate workflow (authz/security suite, SAST, dependency audit, SBOM)
2. Autonomy policy-pack contract gate (schema validation for L0-L5 rules)
3. Release provenance + SBOM artifact generation (signed provenance, dependency inventory, source artifact digest)
4. API compatibility gate
5. Canary smoke checks
6. Fault-injection reliability gate (provider/network/tool fault classes + retry/recovery assertions)
7. Mission queue concurrency/load gate (queue-drain, p95 latency and success-rate SLO assertions)
8. Linux parity gate (runtime/voice/tools/observability API parity on Linux target)
9. Linux installer smoke gate (install/upgrade/channel rollback path on Linux target)
10. Disaster recovery gate (backup + verify + restore drill)
11. Compliance operations gate (access review + incidents + evidence export)

Commands:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/release/check_autonomy_policy_pack.py
git archive --format=tar.gz HEAD -o artifacts/release-source.tar.gz
python scripts/release/generate_release_provenance.py --repo-root . --artifact artifacts/release-source.tar.gz
python scripts/release/api_compat_gate.py
python scripts/release/canary_smoke.py
python scripts/release/fault_injection_reliability_gate.py --retry-max-attempts 2 --scenario-timeout-sec 8 --min-pass-rate-pct 100
python scripts/release/mission_queue_load_gate.py --runs-total 40 --submit-concurrency 8 --worker-count 4 --task-latency-ms 35 --scenario-timeout-sec 30 --min-success-rate-pct 99 --max-failed-runs 0 --max-p95-queue-wait-ms 1500 --max-p95-end-to-end-ms 5000
python scripts/release/linux_parity_smoke.py --iterations 1 --require-linux --output artifacts/linux-parity-smoke-report.json
python scripts/release/linux_installer_smoke.py --require-linux --output artifacts/linux-installer-smoke-report.json
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

3. Re-run smoke + compatibility + Linux parity/installer + disaster-recovery checks.
4. Post incident summary with:
   - failing gate
   - impacted version/tag
   - recovery timestamp
