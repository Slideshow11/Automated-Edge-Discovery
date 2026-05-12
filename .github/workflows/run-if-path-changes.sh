#!/usr/bin/env bash
# run-if-path-changes.sh
# Returns exit 0 if the current commit touches any PR gate file.
# Returns exit 1 otherwise — allowing the CI job to skip.
#
# Usage: run-if-path-changes.sh
# Exits: 0 = relevant changes present, job should run
#        1 = no relevant changes, job should skip
#
# This is read-only: it only reads git diff metadata.

set -euo pipefail

CHANGED_FILES="$(git diff-tree --no-commit-id --name-only -r HEAD)"

PATTERNS=(
  "scripts/local/pr_gate_"
  "tests/test_pr_gate_controller_live_smoke.py"
  ".github/workflows/ci.yml"
  ".github/workflows/ci_check_smoke.py"
)

for pattern in "${PATTERNS[@]}"; do
  if echo "$CHANGED_FILES" | grep -qE "$pattern"; then
    echo "Relevant change detected: $pattern"
    exit 0
  fi
done

echo "No relevant PR gate files changed — skipping pr-gate-live-smoke job"
exit 1