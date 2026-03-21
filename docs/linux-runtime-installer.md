# Linux Runtime Installer

## Purpose

Provide a deterministic install/upgrade path for Linux runtime deployments with explicit release channels.

Installer script:

- `scripts/install_linux.sh`

It creates versioned runtime releases under:

- `${AMARYLLIS_LINUX_INSTALL_ROOT:-$HOME/.local/share/amaryllis}/releases/<release_id>`

and maintains channel pointers:

- `${AMARYLLIS_LINUX_INSTALL_ROOT:-$HOME/.local/share/amaryllis}/channels/stable`
- `${AMARYLLIS_LINUX_INSTALL_ROOT:-$HOME/.local/share/amaryllis}/channels/canary`

with compatibility pointer:

- `${AMARYLLIS_LINUX_INSTALL_ROOT:-$HOME/.local/share/amaryllis}/current`

## Install (fresh machine)

From repository root:

```bash
./scripts/install_linux.sh --channel stable
```

This performs:

1. source sync into a versioned release directory,
2. deterministic bootstrap via `scripts/bootstrap/reproducible_local_bootstrap.sh`,
3. launcher generation at `${AMARYLLIS_LINUX_BIN_DIR:-$HOME/.local/bin}/amaryllis-runtime`,
4. atomic channel pointer switch for selected channel,
5. `current` pointer update when `stable` is installed,
6. optional publish of `artifacts/release-quality-dashboard-final.json` into:
   - `${AMARYLLIS_LINUX_INSTALL_ROOT}/observability/release-quality-dashboard-latest.json`
   when snapshot artifact exists in repo workspace,
7. optional publish of `artifacts/nightly-mission-success-recovery-report.json` into:
   - `${AMARYLLIS_LINUX_INSTALL_ROOT}/observability/nightly-mission-success-recovery-latest.json`
   when nightly mission artifact exists in repo workspace.

## Upgrade

Stable upgrade:

```bash
git pull --ff-only
./scripts/install_linux.sh --channel stable
```

Canary rollout:

```bash
git pull --ff-only
./scripts/install_linux.sh --channel canary
```

Each run creates a new release id and appends it to channel history:

- `${AMARYLLIS_LINUX_INSTALL_ROOT}/channels/<channel>.history`

Old releases are pruned using:

- `AMARYLLIS_KEEP_RELEASES` (default: `3`)

Prune keeps active channel targets (`stable`, `canary`, `current`) to avoid deleting live rollback points.

## Rollback

Channel rollback script:

- `scripts/release/linux_channel_rollback.py`

Rollback one step on canary:

```bash
python3 scripts/release/linux_channel_rollback.py \
  --channel canary \
  --steps 1
```

Rollback stable and update `current`:

```bash
python3 scripts/release/linux_channel_rollback.py \
  --channel stable \
  --steps 1
```

Dry run:

```bash
python3 scripts/release/linux_channel_rollback.py \
  --channel stable \
  --steps 1 \
  --dry-run
```

## Run

Default (stable channel):

```bash
~/.local/bin/amaryllis-runtime
```

Run canary channel:

```bash
AMARYLLIS_LINUX_RELEASE_CHANNEL=canary ~/.local/bin/amaryllis-runtime
```

Environment overrides:

- `AMARYLLIS_LINUX_INSTALL_ROOT` (default install root)
- `AMARYLLIS_LINUX_RELEASE_CHANNEL` (`stable|canary`, runtime launcher channel selector)
- `AMARYLLIS_HOST` (default `127.0.0.1`)
- `AMARYLLIS_PORT` (default `8000`)
- `AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH` (optional override for runtime release-quality snapshot path)
- `AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH` (optional override for runtime nightly mission snapshot path)

## Installer Options

```bash
./scripts/install_linux.sh --help
```

Main flags:

- `--release-id <id>`: explicit deterministic release identifier
- `--channel <stable|canary>`: update selected release channel pointer
- `--skip-bootstrap`: skip bootstrap checks/install (not recommended)
- `--dry-run`: print planned actions only

## CI Validation

Release gate runs Linux installer smoke in blocking mode:

```bash
python scripts/release/linux_installer_smoke.py \
  --require-linux \
  --output artifacts/linux-installer-smoke-report.json
```

Smoke scenario validates:

- stable install + upgrade,
- canary install + upgrade,
- canary rollback via `linux_channel_rollback.py`,
- launcher startup contract for both default and canary channels,
- rollback keeps prior canary release available (no data loss).
