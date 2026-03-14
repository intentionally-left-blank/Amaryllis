# Security and Compliance Operations Baseline

This document defines the operational baseline introduced in TASK-10 for Amaryllis.

Goal: make the runtime auditable against a practical SOC2 / ISO27001 control checklist for a single-node deployment.

## Scope

Covered controls in this baseline:

- secret inventory and rotation posture
- authenticated access and access reviews
- incident lifecycle (open, acknowledge, resolve, evidence)
- signed security actions and audit trail
- exportable audit evidence bundles

Out of scope:

- formal certification process (external auditor evidence package and auditor attestation)
- HSM/KMS integration and centralized enterprise IAM

## Security Operations Endpoints

Admin scope required (`admin` token):

- `GET /security/secrets`
- `POST /security/secrets/sync`
- `GET /security/auth/tokens/activity`
- `POST /security/access-reviews/start`
- `POST /security/access-reviews/{review_id}/complete`
- `GET /security/access-reviews`
- `GET /security/access-reviews/{review_id}`
- `POST /security/incidents/open`
- `POST /security/incidents/{incident_id}/ack`
- `POST /security/incidents/{incident_id}/resolve`
- `POST /security/incidents/{incident_id}/notes`
- `GET /security/incidents`
- `GET /security/incidents/{incident_id}`
- `GET /security/compliance/snapshot`
- `POST /security/compliance/evidence/export`

## Operational Scripts

- Baseline gate check:

```bash
python scripts/security/compliance_check.py
```

- Manual evidence export:

```bash
python scripts/security/export_audit_evidence.py --window-days 90 --event-limit 2000
```

## Control Checklist Mapping

Snapshot/checklist output maps controls to evidence:

- `SOC2-CC6.1`: authentication required in production
- `SOC2-CC6.7`: required secrets present and tracked
- `ISO27001-A.5.17`: runtime identity rotation freshness
- `ISO27001-A.5.18`: periodic access review freshness
- `SOC2-CC7.2`: critical incidents are not left open
- `SOC2-CC7.3`: audit trail quality within failure threshold
- `ISO27001-A.12.7`: evidence storage path is present/writable

## Recommended Operating Cadence

- Secret inventory sync: daily
- Access review: weekly (or max every 30 days)
- Incident review: per incident, with RCA on resolution
- Evidence export: weekly + before release

## Audit Evidence Contents

Exported evidence bundle contains:

- compliance snapshot and control checklist
- filtered security audit events
- access review records
- incident records with event timelines
- auth token activity inventory
- signed action receipt and SHA-256 file digest

## CI and Release Gates

Compliance checks are blocking in CI/release pipelines:

- security policy check
- security auth/authz test suite
- compliance baseline check
- evidence export smoke check

Failure of any gate blocks the release candidate.
