# Release and Rollback Playbook

## Release Gates

Mandatory gates before publish:

1. Security gate workflow (authz/security suite, SAST, dependency audit, SBOM)
2. API compatibility gate
3. Canary smoke checks
4. Disaster recovery gate (backup + verify + restore drill)
5. Compliance operations gate (access review + incidents + evidence export)

Commands:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/release/api_compat_gate.py
python scripts/release/canary_smoke.py
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

3. Re-run smoke + compatibility + disaster-recovery checks.
4. Post incident summary with:
   - failing gate
   - impacted version/tag
   - recovery timestamp
