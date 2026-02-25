#!/usr/bin/env bash
set -euo pipefail

# Setup script for containerized development
# Installs pre-commit hooks and sets up the development environment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_ROOT/.venv"

echo "🚀 Setting up development environment..."
cd "$PROJECT_ROOT"

# Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "📦 Installing uv via pip..."

    if ! command -v python3 &> /dev/null; then
        echo "❌ python3 is required to install uv. Please install uv manually and re-run this script." >&2
        exit 1
    fi

    python3 -m pip install --user --upgrade uv
    UV_USER_BIN="$(python3 -c 'import site; print(site.USER_BASE)')/bin"
    export PATH="$UV_USER_BIN:$PATH"
fi

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    uv venv "$VENV_DIR"
fi

# Sync project + dev dependencies from pyproject.toml/uv.lock
echo "📦 Syncing development dependencies with uv..."
uv sync --extra dev

# Install pre-commit hooks through uv-managed environment
echo "🪝 Installing pre-commit hooks..."
uv run pre-commit install
echo "✅ Pre-commit hooks installed"

echo "✅ Development environment setup complete!"
echo ""
echo "You can now commit your changes. The pre-commit hook will run:"
echo "  - Ruff linting"
echo "  - Ruff format check"
echo "  - MyPy type checking"
