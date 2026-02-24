#!/usr/bin/env bash
set -euo pipefail

# CI script that runs ruff and mypy checks

echo "🔍 Running ruff linting..."
ruff check .

echo "📐 Running ruff format check..."
ruff format --check .

echo "🔎 Running mypy type checking..."
mypy fix_die_repeat tests

echo "✅ All checks passed!"
