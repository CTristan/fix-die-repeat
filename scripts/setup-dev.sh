#!/usr/bin/env bash
set -euo pipefail

# Setup script for containerized development
# Installs pre-commit hooks and sets up the development environment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_ROOT/.venv"

echo "🚀 Setting up development environment..."

# Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "📦 Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    uv venv
fi

# Install development dependencies
echo "📦 Installing development dependencies..."
uv pip install ruff mypy pytest pytest-cov pre-commit

# Install pre-commit hooks (use venv's pre-commit)
echo "🪝 Installing pre-commit hooks..."
"$VENV_DIR/bin/pre-commit" install
echo "✅ Pre-commit hooks installed"

echo "✅ Development environment setup complete!"
echo ""
echo "You can now commit your changes. The pre-commit hook will run:"
echo "  - Ruff linting"
echo "  - Ruff format check"
echo "  - MyPy type checking"
