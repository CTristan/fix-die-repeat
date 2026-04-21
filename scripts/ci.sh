#!/usr/bin/env bash
set -euo pipefail

# CI script that tests, lints, and type-checks the project
#
# Usage:
#   ./scripts/ci.sh              # Run with auto-fix enabled (default)
#   ./scripts/ci.sh --check-only # Run check-only mode (CI-friendly)

CHECK_ONLY=false

if [[ "${1:-}" == "--check-only" ]]; then
    CHECK_ONLY=true
fi

# Use uv run to execute tools from the project's virtual environment
if command -v uv &> /dev/null; then
    RUFF="uv run ruff"
    MYPY="uv run mypy"
    PYTEST="uv run pytest"
    VALIDATE_SCRIPT="uv run scripts/validate_ruff_rules.py"
elif [ -f ".venv/bin/ruff" ]; then
    RUFF=".venv/bin/ruff"
    MYPY=".venv/bin/mypy"
    PYTEST=".venv/bin/pytest"
    VALIDATE_SCRIPT=".venv/bin/python scripts/validate_ruff_rules.py"
else
    RUFF="ruff"
    MYPY="mypy"
    PYTEST="pytest"
    VALIDATE_SCRIPT="python scripts/validate_ruff_rules.py"
fi

# CRITICAL: Validate that prohibited ruff rules are not ignored
# This enforces the NEVER-IGNORE policy
echo "🛡️  Validating ruff rule ignore policy..."
$VALIDATE_SCRIPT

# Preflight: ensure the pi-bridge Node dependencies are installed. The sidecar
# bridge at priv/pi-bridge/ is required for any real `fix-die-repeat` run and
# for the integration test suite. `npm ci` is idempotent when node_modules is
# already present and matches the lockfile.
if [[ -d "priv/pi-bridge" ]]; then
    if command -v npm &> /dev/null; then
        if [[ ! -d "priv/pi-bridge/node_modules" ]]; then
            echo "📦 Installing pi-bridge Node dependencies..."
            (cd priv/pi-bridge && npm ci)
        fi
    else
        echo "⚠️  npm not found on PATH; skipping pi-bridge install (bridge tests will be limited)."
    fi
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
    echo "🔍 Running ruff linting (check-only mode)..."
    $RUFF check .
else
    echo "🔍 Running ruff linting with auto-fix..."
    $RUFF check --fix .
fi

echo "🔎 Running mypy type checking..."
$MYPY fix_die_repeat tests

echo "🧪 Running tests with coverage..."
$PYTEST

if [[ "$CHECK_ONLY" == "true" ]]; then
    echo "📐 Running ruff format check..."
    $RUFF format --check .
else
    echo "📐 Running ruff format (applying changes)..."
    $RUFF format .
fi

echo "✅ All checks passed!"
