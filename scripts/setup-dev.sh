#!/usr/bin/env bash
set -euo pipefail

# Setup script for containerized development
# Installs the pre-commit hook and sets up the development environment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
HOOK_SOURCE="$SCRIPT_DIR/pre-commit-hook.sh"
HOOK_TARGET="$PROJECT_ROOT/.git/hooks/pre-commit"

echo "🚀 Setting up development environment..."

# Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "📦 Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Install development dependencies
echo "📦 Installing development dependencies..."
uv pip install ruff mypy pytest pytest-cov

# Install pre-commit hook
echo "🪝 Installing pre-commit hook..."
if [ -f "$HOOK_SOURCE" ]; then
    cp "$HOOK_SOURCE" "$HOOK_TARGET"
    chmod +x "$HOOK_TARGET"
    echo "✅ Pre-commit hook installed at $HOOK_TARGET"
else
    echo "❌ Pre-commit hook source not found at $HOOK_SOURCE"
    exit 1
fi

echo "✅ Development environment setup complete!"
echo ""
echo "You can now commit your changes. The pre-commit hook will run:"
echo "  - Ruff linting"
echo "  - Ruff format check"
echo "  - MyPy type checking"
