#!/usr/bin/env bash
set -euo pipefail

# Pre-commit hook that runs the CI script
# This ensures all code passes ruff and mypy checks before committing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CI_SCRIPT="$SCRIPT_DIR/ci.sh"

if [ -f "$CI_SCRIPT" ]; then
    echo "🪝 Running pre-commit CI checks..."
    "$CI_SCRIPT"
else
    echo "⚠️  CI script not found at $CI_SCRIPT"
    echo "Skipping pre-commit checks"
    exit 0
fi
