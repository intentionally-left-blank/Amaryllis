#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/install_linux.sh [options]

Deterministic Linux runtime installer/upgrade path for Amaryllis.

Options:
  --release-id <id>      Use explicit release identifier.
  --channel <name>       Release channel to update (stable|canary, default: stable).
  --skip-bootstrap       Skip bootstrap checks/install step (not recommended).
  --dry-run              Print actions without changing filesystem.
  --help                 Show this help.

Environment:
  AMARYLLIS_LINUX_INSTALL_ROOT      Install root (default: $HOME/.local/share/amaryllis)
  AMARYLLIS_LINUX_BIN_DIR           Launcher dir (default: $HOME/.local/bin)
  AMARYLLIS_BOOTSTRAP_PYTHON        Python executable for bootstrap (default: python3.11)
  AMARYLLIS_KEEP_RELEASES           Number of releases to keep (default: 3)
  AMARYLLIS_LINUX_RELEASE_CHANNEL   Default channel when --channel is omitted (stable|canary)
  AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH  Runtime export path for release quality snapshot
  AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH     Runtime export path for nightly mission report snapshot
USAGE
}

validate_channel() {
  local value="$1"
  case "${value}" in
    stable|canary)
      return 0
      ;;
    *)
      echo "[linux-installer] invalid channel: ${value} (expected stable|canary)" >&2
      exit 2
      ;;
  esac
}

append_history() {
  local history_path="$1"
  local release_id="$2"
  local last_entry=""

  if [[ -f "${history_path}" ]]; then
    last_entry="$(tail -n 1 "${history_path}" 2>/dev/null || true)"
  fi

  if [[ "${last_entry}" == "${release_id}" ]]; then
    return 0
  fi

  printf '%s\n' "${release_id}" >> "${history_path}"
}

DRY_RUN=0
SKIP_BOOTSTRAP=0
RELEASE_ID=""
CHANNEL="${AMARYLLIS_LINUX_RELEASE_CHANNEL:-stable}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-id)
      if [[ $# -lt 2 ]]; then
        echo "[linux-installer] --release-id requires a value" >&2
        exit 2
      fi
      RELEASE_ID="$2"
      shift 2
      ;;
    --channel)
      if [[ $# -lt 2 ]]; then
        echo "[linux-installer] --channel requires a value" >&2
        exit 2
      fi
      CHANNEL="$2"
      shift 2
      ;;
    --skip-bootstrap)
      SKIP_BOOTSTRAP=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "[linux-installer] unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

validate_channel "${CHANNEL}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "[linux-installer] this installer only supports Linux hosts" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "${ROOT_DIR}/runtime/server.py" ]]; then
  echo "[linux-installer] runtime source not found under: ${ROOT_DIR}" >&2
  exit 1
fi

INSTALL_ROOT="${AMARYLLIS_LINUX_INSTALL_ROOT:-${HOME}/.local/share/amaryllis}"
BIN_DIR="${AMARYLLIS_LINUX_BIN_DIR:-${HOME}/.local/bin}"
PYTHON_BIN="${AMARYLLIS_BOOTSTRAP_PYTHON:-python3.11}"
KEEP_RELEASES="${AMARYLLIS_KEEP_RELEASES:-3}"

if [[ "${DRY_RUN}" != "1" ]]; then
  if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    echo "[linux-installer] python executable not found: ${PYTHON_BIN}" >&2
    echo "[linux-installer] install pinned python first or set AMARYLLIS_BOOTSTRAP_PYTHON" >&2
    exit 1
  fi
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "[linux-installer] rsync is required" >&2
  exit 1
fi

git_rev="$(git -C "${ROOT_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
if [[ -z "${git_rev}" ]]; then
  git_rev="nogit"
fi
if [[ -z "${RELEASE_ID}" ]]; then
  RELEASE_ID="$(date -u +"%Y%m%d%H%M%S")-${git_rev}"
fi

RELEASES_DIR="${INSTALL_ROOT}/releases"
CHANNELS_DIR="${INSTALL_ROOT}/channels"
CHANNEL_LINK="${CHANNELS_DIR}/${CHANNEL}"
CHANNEL_HISTORY="${CHANNELS_DIR}/${CHANNEL}.history"
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
SRC_DIR="${RELEASE_DIR}/src"
VENV_DIR="${RELEASE_DIR}/venv"
CURRENT_LINK="${INSTALL_ROOT}/current"
LAUNCHER="${BIN_DIR}/amaryllis-runtime"
RELEASE_QUALITY_SNAPSHOT_SOURCE="${ROOT_DIR}/artifacts/release-quality-dashboard-final.json"
RELEASE_QUALITY_TREND_SOURCE="${ROOT_DIR}/artifacts/release-quality-dashboard-trend-final.json"
RELEASE_QUALITY_RUNTIME_PATH="${INSTALL_ROOT}/observability/release-quality-dashboard-latest.json"
RELEASE_QUALITY_PUBLISHER="${ROOT_DIR}/scripts/release/publish_release_quality_snapshot.py"
NIGHTLY_MISSION_REPORT_SOURCE="${ROOT_DIR}/artifacts/nightly-mission-success-recovery-report.json"
NIGHTLY_MISSION_RUNTIME_PATH="${INSTALL_ROOT}/observability/nightly-mission-success-recovery-latest.json"
NIGHTLY_MISSION_PUBLISHER="${ROOT_DIR}/scripts/release/publish_mission_success_recovery_snapshot.py"

run_cmd() {
  echo "+ $*"
  if [[ "${DRY_RUN}" == "1" ]]; then
    return 0
  fi
  "$@"
}

echo "[linux-installer] root: ${ROOT_DIR}"
echo "[linux-installer] install root: ${INSTALL_ROOT}"
echo "[linux-installer] release id: ${RELEASE_ID}"
echo "[linux-installer] channel: ${CHANNEL}"
echo "[linux-installer] dry run: ${DRY_RUN}"

if [[ "${DRY_RUN}" != "1" && -e "${RELEASE_DIR}" ]]; then
  echo "[linux-installer] release already exists: ${RELEASE_DIR}" >&2
  exit 1
fi

run_cmd mkdir -p "${RELEASES_DIR}" "${CHANNELS_DIR}" "${BIN_DIR}" "${SRC_DIR}"
run_cmd rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.build' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude 'macos/AmaryllisApp/.build' \
  --exclude 'macos/AmaryllisApp/dist' \
  "${ROOT_DIR}/" "${SRC_DIR}/"

if [[ "${SKIP_BOOTSTRAP}" == "1" ]]; then
  echo "[linux-installer] bootstrap step skipped (--skip-bootstrap)"
else
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "+ (cd \"${SRC_DIR}\" && AMARYLLIS_BOOTSTRAP_VENV=\"${VENV_DIR}\" AMARYLLIS_BOOTSTRAP_PYTHON=\"${PYTHON_BIN}\" ./scripts/bootstrap/reproducible_local_bootstrap.sh)"
  else
    (
      cd "${SRC_DIR}"
      AMARYLLIS_BOOTSTRAP_VENV="${VENV_DIR}" \
      AMARYLLIS_BOOTSTRAP_PYTHON="${PYTHON_BIN}" \
      ./scripts/bootstrap/reproducible_local_bootstrap.sh
    )
  fi
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "+ write launcher ${LAUNCHER}"
else
  cat > "${LAUNCHER}" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${AMARYLLIS_LINUX_INSTALL_ROOT:-${HOME}/.local/share/amaryllis}"
CHANNEL="${AMARYLLIS_LINUX_RELEASE_CHANNEL:-stable}"
CHANNEL_LINK="${INSTALL_ROOT}/channels/${CHANNEL}"

if [[ -L "${CHANNEL_LINK}" ]]; then
  CURRENT="${CHANNEL_LINK}"
else
  CURRENT="${INSTALL_ROOT}/current"
fi

SRC_DIR="${CURRENT}/src"
VENV_DIR="${CURRENT}/venv"

if [[ ! -x "${VENV_DIR}/bin/uvicorn" ]]; then
  echo "[amaryllis-runtime] uvicorn not found in ${VENV_DIR}; run installer again." >&2
  exit 1
fi

HOST="${AMARYLLIS_HOST:-127.0.0.1}"
PORT="${AMARYLLIS_PORT:-8000}"
RELEASE_QUALITY_DASHBOARD_PATH="${AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH:-${INSTALL_ROOT}/observability/release-quality-dashboard-latest.json}"
export AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH="${RELEASE_QUALITY_DASHBOARD_PATH}"
NIGHTLY_MISSION_REPORT_PATH="${AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH:-${INSTALL_ROOT}/observability/nightly-mission-success-recovery-latest.json}"
export AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH="${NIGHTLY_MISSION_REPORT_PATH}"

exec "${VENV_DIR}/bin/uvicorn" runtime.server:app \
  --app-dir "${SRC_DIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  "$@"
LAUNCHER
  chmod +x "${LAUNCHER}"
fi

run_cmd ln -sfn "${RELEASE_DIR}" "${CHANNEL_LINK}"

if [[ "${DRY_RUN}" == "1" ]]; then
  echo "+ append ${RELEASE_ID} to ${CHANNEL_HISTORY}"
else
  append_history "${CHANNEL_HISTORY}" "${RELEASE_ID}"
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  if [[ "${CHANNEL}" == "stable" ]]; then
    echo "+ ln -sfn ${RELEASE_DIR} ${CURRENT_LINK}"
  else
    echo "+ preserve ${CURRENT_LINK} (stable stays active by default)"
  fi
else
  if [[ "${CHANNEL}" == "stable" ]]; then
    ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"
  elif [[ ! -e "${CURRENT_LINK}" ]]; then
    ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"
  fi
fi

if [[ "${DRY_RUN}" != "1" ]]; then
  if [[ "${KEEP_RELEASES}" =~ ^[0-9]+$ ]] && [[ "${KEEP_RELEASES}" -ge 1 ]]; then
    mapfile -t all_releases < <(ls -1dt "${RELEASES_DIR}"/* 2>/dev/null || true)
    declare -A protected_targets=()
    for link_path in "${CURRENT_LINK}" "${CHANNELS_DIR}/stable" "${CHANNELS_DIR}/canary"; do
      if [[ -L "${link_path}" ]]; then
        resolved="$(readlink -f "${link_path}" 2>/dev/null || true)"
        if [[ -n "${resolved}" ]]; then
          protected_targets["${resolved}"]=1
        fi
      fi
    done

    if [[ "${#all_releases[@]}" -gt "${KEEP_RELEASES}" ]]; then
      for ((i=KEEP_RELEASES; i<${#all_releases[@]}; i++)); do
        candidate="${all_releases[$i]}"
        resolved_candidate="$(readlink -f "${candidate}" 2>/dev/null || true)"
        if [[ -n "${resolved_candidate}" && -n "${protected_targets[${resolved_candidate}]:-}" ]]; then
          echo "[linux-installer] preserving active release: ${candidate}"
          continue
        fi
        echo "[linux-installer] pruning old release: ${candidate}"
        rm -rf "${candidate}"
      done
    fi
  else
    echo "[linux-installer] invalid AMARYLLIS_KEEP_RELEASES=${KEEP_RELEASES}, skipping prune"
  fi
fi

if [[ -f "${RELEASE_QUALITY_SNAPSHOT_SOURCE}" ]]; then
  publish_cmd=(
    "${PYTHON_BIN}"
    "${RELEASE_QUALITY_PUBLISHER}"
    "--snapshot-report"
    "${RELEASE_QUALITY_SNAPSHOT_SOURCE}"
    "--install-root"
    "${INSTALL_ROOT}"
  )
  if [[ -f "${RELEASE_QUALITY_TREND_SOURCE}" ]]; then
    publish_cmd+=("--trend-report" "${RELEASE_QUALITY_TREND_SOURCE}")
  fi
  run_cmd "${publish_cmd[@]}"
else
  echo "[linux-installer] release quality snapshot not found: ${RELEASE_QUALITY_SNAPSHOT_SOURCE} (skip publish)"
fi

if [[ -f "${NIGHTLY_MISSION_REPORT_SOURCE}" ]]; then
  nightly_publish_cmd=(
    "${PYTHON_BIN}"
    "${NIGHTLY_MISSION_PUBLISHER}"
    "--report"
    "${NIGHTLY_MISSION_REPORT_SOURCE}"
    "--channel"
    "nightly"
    "--expect-scope"
    "nightly"
    "--install-root"
    "${INSTALL_ROOT}"
  )
  run_cmd "${nightly_publish_cmd[@]}"
else
  echo "[linux-installer] nightly mission report not found: ${NIGHTLY_MISSION_REPORT_SOURCE} (skip publish)"
fi

echo "[linux-installer] install complete"
echo "[linux-installer] launcher: ${LAUNCHER}"
if [[ -L "${CHANNEL_LINK}" ]]; then
  echo "[linux-installer] channel ${CHANNEL}: ${CHANNEL_LINK} -> $(readlink -f "${CHANNEL_LINK}")"
fi
if [[ -L "${CURRENT_LINK}" ]]; then
  echo "[linux-installer] current release: ${CURRENT_LINK} -> $(readlink -f "${CURRENT_LINK}")"
else
  echo "[linux-installer] current release link not set"
fi
echo "[linux-installer] release quality runtime path: ${RELEASE_QUALITY_RUNTIME_PATH}"
echo "[linux-installer] nightly mission runtime path: ${NIGHTLY_MISSION_RUNTIME_PATH}"
echo "[linux-installer] start runtime: ${LAUNCHER}"
