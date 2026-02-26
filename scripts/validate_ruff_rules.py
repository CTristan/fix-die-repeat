#!/usr/bin/env python3
"""Validate that no prohibited ruff rules are ignored in pyproject.toml.

This script enforces the NEVER-IGNORE policy for specific ruff rules:
- C901: Complex functions must be refactored
- PLR0913: Too many arguments must be grouped
- PLR2004: Magic values must be named constants
- PLC0415: Imports must be at module top-level

Usage:
    scripts/validate_ruff_rules.py
    uv run scripts/validate_ruff_rules.py

Exit codes:
    0: All checks passed
    1: Prohibited rules found in per-file-ignores
    2: pyproject.toml not found or invalid
"""

import sys
from pathlib import Path

# Import shared validation logic from utils
from fix_die_repeat.utils import (
    PROHIBITED_RUFF_RULES,
    RuffConfigParseError,
    find_prohibited_ruff_ignores,
)

# Rationale for each prohibition
RATIONALE = {
    "C901": "Complex functions → Extract helper methods or use Strategy pattern",
    "PLR0913": "Too many arguments → Group into dataclass, NamedTuple, or use **kwargs for Click",
    "PLR2004": "Magic values → Use named constants at module level",
    "PLC0415": "Imports outside top-level → Move to module top or refactor circular deps",
}


def main() -> int:
    """Validate pyproject.toml for prohibited ruff rule ignores.

    Returns:
        0 if validation passes, 1 if prohibited ignores found, 2 on config errors

    """
    pyproject_path = Path("pyproject.toml")

    if not pyproject_path.exists():
        print(f"ERROR: {pyproject_path} not found")
        return 2

    # Check for prohibited ignores using shared logic
    try:
        violations = find_prohibited_ruff_ignores(pyproject_path, PROHIBITED_RUFF_RULES)
    except RuffConfigParseError as e:
        print(f"ERROR: {e}")
        return 2

    if violations:
        print("=" * 70)
        print("ERROR: Prohibited ruff rules found in per-file-ignores!")
        print("=" * 70)
        print()
        print("The following rules MUST NEVER be ignored (see AGENTS.md):")
        for rule in sorted(PROHIBITED_RUFF_RULES):
            print(f"  - {rule}: {RATIONALE[rule]}")
        print()
        print("Violations found:")
        for file_pattern, rules in sorted(violations.items()):
            print(f"  {file_pattern}:")
            for rule in sorted(rules):
                print(f"    - {rule}")
        print()
        print("To fix:")
        print("  1. Remove the ignore from pyproject.toml")
        print("  2. Refactor the code to address the underlying issue")
        print()
        print("Refactoring strategies:")
        print("  • C901: Extract helper functions or use design patterns")
        print("  • PLR0913: Group arguments into dataclass or NamedTuple")
        print("  • PLR2004: Replace magic values with named constants")
        print("  • PLC0415: Move imports to module top, refactor circular deps")
        print()
        return 1

    print("✓ No prohibited ruff rules in per-file-ignores")
    return 0


if __name__ == "__main__":
    sys.exit(main())
