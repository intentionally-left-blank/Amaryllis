# Disaster Recovery

## Scope

Amaryllis stores runtime state in:

- SQLite database (`AMARYLLIS_DATABASE_PATH`)
- vector index/meta files (`AMARYLLIS_VECTOR_INDEX_PATH`, `*.meta.json`)
- local identity bundle (`AMARYLLIS_IDENTITY_PATH`)

Task 09 introduces production-grade backup + restore controls for these assets.

## Backup Strategy

- Regular scheduled backups via runtime backup scheduler.
- Manual on-demand backups via API or CLI scripts.
- Every backup archive includes:
  - `manifest.json` with file checksums and DB snapshot stats
  - `data/` snapshot (including SQLite copy and vector files)
- Optional verification on create (`AMARYLLIS_BACKUP_VERIFY_ON_CREATE=true`).
- Retention policy by count and age:
  - `AMARYLLIS_BACKUP_RETENTION_COUNT`
  - `AMARYLLIS_BACKUP_RETENTION_DAYS`

## Runtime Endpoints (Service Scope)

- `GET /service/backup/status`
- `GET /service/backup/backups`
- `POST /service/backup/run`
- `POST /service/backup/verify`
- `POST /service/backup/restore-drill`

All endpoints require `service` or `admin` scope and include signed action receipts.

## CLI Operations

Manual backup:

```bash
python scripts/disaster_recovery/backup_now.py --trigger manual-cli --verify true
```

Restore drill (non-destructive):

```bash
python scripts/disaster_recovery/restore_drill.py
```

Restore from archive (runtime must be stopped):

```bash
python scripts/disaster_recovery/restore_from_archive.py \
  --archive /path/to/backup.tar.gz \
  --preserve-existing true
```

## Recovery Workflow

1. Stop runtime.
2. Verify target archive (`/service/backup/verify` or CLI verify in restore script).
3. Restore into data directory.
4. Start runtime.
5. Validate:
   - `/health`
   - `/service/health`
   - `/service/observability/slo`
6. If needed, roll back to generated rollback directory.

## RTO / RPO Guidance

- **RPO** is bounded by backup interval (`AMARYLLIS_BACKUP_INTERVAL_SEC`).
- **RTO** is bounded by restore time + restart + health verification.
- Run regular restore drills to keep RTO predictable.
