#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV_DIR="${AMARYLLIS_BOOTSTRAP_VENV:-${ROOT_DIR}/.venv}"
PYTHON_BIN="${AMARYLLIS_BOOTSTRAP_PYTHON:-python3.11}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "[bootstrap] python executable not found: ${PYTHON_BIN}" >&2
  echo "[bootstrap] install pinned toolchain python first or set AMARYLLIS_BOOTSTRAP_PYTHON explicitly" >&2
  exit 2
fi

echo "[bootstrap] project root: ${ROOT_DIR}"
echo "[bootstrap] python: $(${PYTHON_BIN} --version)"
echo "[bootstrap] validating toolchain drift guard"
"${PYTHON_BIN}" "${ROOT_DIR}/scripts/release/check_toolchain_drift.py" \
  --repo-root "${ROOT_DIR}" \
  --check-python-executable "${PYTHON_BIN}"

echo "[bootstrap] creating virtualenv: ${VENV_DIR}"
"${PYTHON_BIN}" -m venv "${VENV_DIR}"

VENV_PYTHON="${VENV_DIR}/bin/python"
if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[bootstrap] failed to create virtualenv python at ${VENV_PYTHON}" >&2
  exit 1
fi

echo "[bootstrap] upgrading pip"
"${VENV_PYTHON}" -m pip install --upgrade pip

echo "[bootstrap] installing deterministic dependencies from requirements.lock"
"${VENV_PYTHON}" -m pip install -r "${ROOT_DIR}/requirements.lock"

echo "[bootstrap] validating dependency drift guard"
"${VENV_PYTHON}" "${ROOT_DIR}/scripts/release/check_dependency_drift.py"

echo "[bootstrap] validating runtime/SLO profile drift guard"
"${VENV_PYTHON}" "${ROOT_DIR}/scripts/release/check_runtime_profile_drift.py"

echo "[bootstrap] validating golden task suite schema"
"${VENV_PYTHON}" "${ROOT_DIR}/scripts/eval/run_golden_tasks.py" --validate-only

echo "[bootstrap] OK"
echo "[bootstrap] activate environment: source \"${VENV_DIR}/bin/activate\""
echo "[bootstrap] run runtime: uvicorn runtime.server:app --host localhost --port 8000 --reload"
