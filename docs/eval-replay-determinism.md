# Eval/Replay Determinism (P2-B03)

Amaryllis now enforces deterministic eval and replay reproducibility through seeded execution and fixture snapshots.

## Deterministic Golden Eval

Use fixture-driven mode in `scripts/eval/run_golden_tasks.py`:

- `--seed <int>`: deterministic seed metadata
- `--fixture-responses <json>`: offline responses by task id (no network)
- `--snapshot-expected <json>`: expected canonical snapshot to detect drift
- `--update-snapshot`: rewrite expected snapshot fixture

Reference fixtures:

- `eval/golden_tasks/deterministic_smoke.json`
- `eval/fixtures/golden_tasks/deterministic_smoke_responses.json`
- `eval/fixtures/golden_tasks/deterministic_smoke_snapshot.json`

## Deterministic Replay Snapshot

Canonical replay snapshots are produced via:

- `eval/replay_snapshot.py` (`canonicalize_replay_snapshot`)

Reference fixtures:

- `eval/fixtures/replay/sample_replay_input.json`
- `eval/fixtures/replay/sample_replay_snapshot.json`

## Unified Gate

Blocking reproducibility gate:

```bash
python scripts/release/check_eval_replay_determinism.py
```

It validates both:

- seeded golden eval snapshot drift
- replay canonical snapshot drift

Optional fixture update mode:

```bash
python scripts/release/check_eval_replay_determinism.py --update-fixtures
```

## CI/Bootstrap Integration

The deterministic eval/replay gate is wired into:

- `scripts/bootstrap/reproducible_local_bootstrap.sh`
- `.github/workflows/release-gate.yml`
- `.github/workflows/security-gate.yml`
- `.github/workflows/nightly-reliability.yml`
