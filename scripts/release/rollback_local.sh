#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <git-tag-or-commit>"
  exit 2
fi

TARGET_REF="$1"

echo "[rollback] switching runtime source to ${TARGET_REF}"
git fetch --all --tags
git checkout "${TARGET_REF}"

echo "[rollback] reinstalling runtime dependencies"
python3 -m pip install -r requirements.txt

echo "[rollback] validating runtime boots"
python3 -m unittest tests.test_security_http_authz -v
python3 -m unittest tests.test_security_compliance_api -v
python3 scripts/security/compliance_check.py

echo "[rollback] done - pin this ref as rollback baseline"
