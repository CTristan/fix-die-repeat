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
elif [ -f ".venv/bin/ruff" ]; then
    RUFF=".venv/bin/ruff"
    MYPY=".venv/bin/mypy"
    PYTEST=".venv/bin/pytest"
else
    RUFF="ruff"
    MYPY="mypy"
    PYTEST="pytest"
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
