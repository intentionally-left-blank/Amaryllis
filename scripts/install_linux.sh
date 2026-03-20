#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_linux.sh [options]

Deterministic Linux runtime installer/upgrade path for Amaryllis.

Options:
  --release-id <id>      Use explicit release identifier.
  --skip-bootstrap       Skip bootstrap checks/install step (not recommended).
  --dry-run              Print actions without changing filesystem.
  --help                 Show this help.

Environment:
  AMARYLLIS_LINUX_INSTALL_ROOT  Install root (default: $HOME/.local/share/amaryllis)
  AMARYLLIS_LINUX_BIN_DIR       Launcher dir (default: $HOME/.local/bin)
  AMARYLLIS_BOOTSTRAP_PYTHON    Python executable for bootstrap (default: python3.11)
  AMARYLLIS_KEEP_RELEASES       Number of releases to keep (default: 3)
EOF
}

DRY_RUN=0
SKIP_BOOTSTRAP=0
RELEASE_ID=""

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
RELEASE_DIR="${RELEASES_DIR}/${RELEASE_ID}"
SRC_DIR="${RELEASE_DIR}/src"
VENV_DIR="${RELEASE_DIR}/venv"
CURRENT_LINK="${INSTALL_ROOT}/current"
LAUNCHER="${BIN_DIR}/amaryllis-runtime"

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
echo "[linux-installer] dry run: ${DRY_RUN}"

if [[ "${DRY_RUN}" != "1" && -e "${RELEASE_DIR}" ]]; then
  echo "[linux-installer] release already exists: ${RELEASE_DIR}" >&2
  exit 1
fi

run_cmd mkdir -p "${RELEASES_DIR}" "${BIN_DIR}" "${SRC_DIR}"
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
  cat > "${LAUNCHER}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${AMARYLLIS_LINUX_INSTALL_ROOT:-${HOME}/.local/share/amaryllis}"
CURRENT="${INSTALL_ROOT}/current"
SRC_DIR="${CURRENT}/src"
VENV_DIR="${CURRENT}/venv"

if [[ ! -x "${VENV_DIR}/bin/uvicorn" ]]; then
  echo "[amaryllis-runtime] uvicorn not found in ${VENV_DIR}; run installer again." >&2
  exit 1
fi

HOST="${AMARYLLIS_HOST:-127.0.0.1}"
PORT="${AMARYLLIS_PORT:-8000}"

exec "${VENV_DIR}/bin/uvicorn" runtime.server:app \
  --app-dir "${SRC_DIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  "$@"
EOF
  chmod +x "${LAUNCHER}"
fi

run_cmd ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"

if [[ "${DRY_RUN}" != "1" ]]; then
  if [[ "${KEEP_RELEASES}" =~ ^[0-9]+$ ]] && [[ "${KEEP_RELEASES}" -ge 1 ]]; then
    mapfile -t all_releases < <(ls -1dt "${RELEASES_DIR}"/* 2>/dev/null || true)
    if [[ "${#all_releases[@]}" -gt "${KEEP_RELEASES}" ]]; then
      for ((i=KEEP_RELEASES; i<${#all_releases[@]}; i++)); do
        echo "[linux-installer] pruning old release: ${all_releases[$i]}"
        rm -rf "${all_releases[$i]}"
      done
    fi
  else
    echo "[linux-installer] invalid AMARYLLIS_KEEP_RELEASES=${KEEP_RELEASES}, skipping prune"
  fi
fi

echo "[linux-installer] install complete"
echo "[linux-installer] launcher: ${LAUNCHER}"
echo "[linux-installer] current release: ${CURRENT_LINK} -> ${RELEASE_DIR}"
echo "[linux-installer] start runtime: ${LAUNCHER}"
