# Linux Runtime Installer

## Purpose

Provide a deterministic install/upgrade path for Linux runtime deployments.

Installer script:

- `scripts/install_linux.sh`

It creates versioned runtime releases under:

- `${AMARYLLIS_LINUX_INSTALL_ROOT:-$HOME/.local/share/amaryllis}/releases/<release_id>`

and atomically switches:

- `${AMARYLLIS_LINUX_INSTALL_ROOT:-$HOME/.local/share/amaryllis}/current`

## Install (fresh machine)

From repository root:

```bash
./scripts/install_linux.sh
```

This performs:

1. source sync into a versioned release directory,
2. deterministic bootstrap via `scripts/bootstrap/reproducible_local_bootstrap.sh`,
3. launcher generation at `${AMARYLLIS_LINUX_BIN_DIR:-$HOME/.local/bin}/amaryllis-runtime`,
4. atomic `current` symlink switch.

## Upgrade

Run the same installer again after updating repository source:

```bash
git pull --ff-only
./scripts/install_linux.sh
```

Each run creates a new release id and updates `current` to the latest release.

Old releases are pruned using:

- `AMARYLLIS_KEEP_RELEASES` (default: `3`)

## Run

```bash
~/.local/bin/amaryllis-runtime
```

Environment overrides:

- `AMARYLLIS_LINUX_INSTALL_ROOT` (default install root)
- `AMARYLLIS_HOST` (default `127.0.0.1`)
- `AMARYLLIS_PORT` (default `8000`)

## Installer Options

```bash
./scripts/install_linux.sh --help
```

Main flags:

- `--release-id <id>`: explicit deterministic release identifier
- `--skip-bootstrap`: skip bootstrap checks/install (not recommended)
- `--dry-run`: print planned actions only

