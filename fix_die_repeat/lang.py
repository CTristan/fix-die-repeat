"""Language detection from file extensions.

This module provides functions to detect programming languages from file paths
based on their extensions. It's used to add language-specific checks to the review
and fix prompts.
"""

from pathlib import PurePosixPath

# Mapping of file extensions to canonical language keys.
# These keys correspond to partial template filenames (e.g., python.j2).
LANGUAGE_EXTENSIONS: dict[str, str] = {
    # Python
    ".py": "python",
    ".pyi": "python",
    # Rust
    ".rs": "rust",
    # JavaScript / TypeScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    # Elixir / Phoenix
    ".ex": "elixir",
    ".exs": "elixir",
    ".heex": "elixir",
    ".leex": "elixir",
    # C#
    ".cs": "csharp",
    ".csx": "csharp",
}


def detect_languages_from_files(changed_files: list[str]) -> set[str]:
    """Detect languages from a list of file paths using extension mapping.

    Args:
        changed_files: List of file paths (relative to project root),
                       as returned by utils.get_changed_files()

    Returns:
        Set of canonical language keys (e.g., {"python", "rust"})

    """
    detected: set[str] = set()

    for filepath in changed_files:
        # Extract extension using PurePosixPath for consistent path handling
        # even on Windows platforms
        extension = PurePosixPath(filepath).suffix.lower()

        # Map extension to language key
        language = LANGUAGE_EXTENSIONS.get(extension)
        if language:
            detected.add(language)

    return detected


def resolve_languages(
    changed_files: list[str],
    override: str | None = None,
) -> set[str]:
    """Resolve languages using hybrid strategy: config override + diff detection.

    If override is provided (comma-separated string, e.g., "python,rust"),
    it REPLACES diff detection entirely. This is intentional — the override
    exists for edge cases where diff detection is wrong or for testing.

    Args:
        changed_files: List of changed file paths
        override: Optional comma-separated language override (from FDR_LANGUAGES)

    Returns:
        Set of canonical language keys

    """
    if override:
        # Override completely replaces detection
        # Split on comma, strip whitespace, filter empty strings
        languages = {lang.strip() for lang in override.split(",") if lang.strip()}
        # If override was provided but parsed to empty (e.g., only whitespace),
        # fall back to detection
        if languages:
            return languages

    # No override or override was empty/whitespace: use diff-based detection
    return detect_languages_from_files(changed_files)
