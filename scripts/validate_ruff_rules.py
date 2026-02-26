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

    # Read and parse pyproject.toml
    try:
        config_text = pyproject_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"ERROR: Could not read {pyproject_path}: {e}")
        return 2

    # Parse TOML (simple approach - look for per-file-ignores section)
    # We use a simple text-based approach to avoid adding tomllib dependency
    violations = _check_prohibited_ignores(config_text)

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


def _is_section_header(stripped: str) -> bool:
    """Check if line is the per-file-ignores section header.

    Args:
        stripped: Stripped line from config file

    Returns:
        True if this is the section header we're looking for

    """
    return stripped == "[tool.ruff.lint.per-file-ignores]"


def _should_exit_section(
    stripped: str,
    *,
    in_section: bool,
) -> bool:
    """Check if we should exit the per-file-ignores section.

    Args:
        stripped: Stripped line from config file
        in_section: Whether we're currently in the section

    Returns:
        True if we should exit the section

    """
    return (
        stripped.startswith("[")
        and "]" in stripped
        and in_section
        and not any(key in stripped for key in ["per-file-ignores", "ruff"])
    )


def _extract_rules_from_line(stripped: str) -> tuple[str, str] | None:
    """Extract pattern and rules string from a config line.

    Args:
        stripped: Stripped line from config file

    Returns:
        Tuple of (pattern, rules_str) if valid, None otherwise

    """
    if not ("=" in stripped and not stripped.startswith("#")):
        return None

    parts_count = 2
    parts = stripped.split("=", 1)
    if len(parts) != parts_count:
        return None

    pattern = parts[0].strip().strip('"')
    rules_str = parts[1].strip()

    # Extract rule codes from list
    if rules_str.startswith("[") and rules_str.endswith("]"):
        rules_str = rules_str[1:-1]

    return pattern, rules_str


def _find_prohibited_rules_in_line(
    pattern: str,
    rules_str: str,
) -> dict[str, set[str]]:
    """Check a line for prohibited rules and build violations dict.

    Args:
        pattern: File pattern from config line
        rules_str: Rules string from config line

    Returns:
        Dict with violations found (or empty if none)

    """
    violations: dict[str, set[str]] = {}

    for rule in PROHIBITED_RULES:
        if f'"{rule}"' in rules_str or f"'{rule}'" in rules_str:
            if pattern not in violations:
                violations[pattern] = set()
            violations[pattern].add(rule)

    return violations


def _check_prohibited_ignores(config_text: str) -> dict[str, set[str]]:
    """Check config text for prohibited per-file ignores.

    Args:
        config_text: Contents of pyproject.toml

    Returns:
        Dict mapping file patterns to sets of prohibited rule codes found

    """
    violations: dict[str, set[str]] = {}

    # Simple parsing: find [tool.ruff.lint.per-file-ignores] section
    lines = config_text.splitlines()

    in_section = False

    for line in lines:
        stripped = line.strip()

        # Check for section header
        if _is_section_header(stripped):
            in_section = True
            continue

        # Exit section when we hit another section
        if _should_exit_section(stripped, in_section=in_section):
            in_section = False
            continue

        if not in_section:
            continue

        # Parse pattern = ["rule1", "rule2"] lines
        line_result = _extract_rules_from_line(stripped)
        if line_result:
            pattern, rules_str = line_result
            line_violations = _find_prohibited_rules_in_line(pattern, rules_str)
            violations.update(line_violations)

    return violations


if __name__ == "__main__":
    sys.exit(main())
