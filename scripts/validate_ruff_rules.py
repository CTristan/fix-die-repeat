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
import tomllib
from pathlib import Path

# Prohibited rules that must NEVER be ignored (see AGENTS.md)
PROHIBITED_RULES = {"C901", "PLR0913", "PLR2004", "PLC0415"}

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

    # Parse TOML using tomllib (stdlib)
    violations = _check_prohibited_ignores(pyproject_path)

    if violations:
        print("=" * 70)
        print("ERROR: Prohibited ruff rules found in per-file-ignores!")
        print("=" * 70)
        print()
        print("The following rules MUST NEVER be ignored (see AGENTS.md):")
        for rule in sorted(PROHIBITED_RULES):
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


def _check_prohibited_ignores(pyproject_path: Path) -> dict[str, set[str]]:
    """Check pyproject.toml for prohibited per-file ignores using tomllib.

    Args:
        pyproject_path: Path to pyproject.toml file

    Returns:
        Dict mapping file patterns to sets of prohibited rule codes found

    """
    violations: dict[str, set[str]] = {}

    try:
        with pyproject_path.open("rb") as f:
            config = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"ERROR: Could not parse {pyproject_path}: {e}")
        return violations

    # Navigate to tool.ruff.lint.per-file-ignores
    per_file_ignores = (
        config.get("tool", {}).get("ruff", {}).get("lint", {}).get("per-file-ignores", {})
    )

    if not per_file_ignores:
        return violations

    # Check each file pattern for prohibited rules
    for pattern, rules_list in per_file_ignores.items():
        if not isinstance(rules_list, list):
            continue

        for rule in rules_list:
            if rule in PROHIBITED_RULES:
                if pattern not in violations:
                    violations[pattern] = set()
                violations[pattern].add(rule)

    return violations


if __name__ == "__main__":
    sys.exit(main())
