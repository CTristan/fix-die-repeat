"""Constants and message generators for user-facing messages."""

from collections.abc import Iterable


def git_diff_instructions(start_sha: str) -> str:
    """Return git diff/checkout instructions for showing changes.

    Args:
        start_sha: Starting git commit SHA

    Returns:
        Instruction string

    """
    return f"To see all changes made: git diff {start_sha}"


def git_checkout_instructions(start_sha: str) -> str:
    """Return git checkout instructions for rolling back changes.

    Args:
        start_sha: Starting git commit SHA

    Returns:
        Instruction string

    """
    return f"To revert all changes:   git checkout {start_sha} -- ."


def oscillation_warning(prev_iter: int) -> str:
    """Return warning for detected oscillation (identical check output).

    Args:
        prev_iter: Previous iteration number that matched

    Returns:
        Warning message

    """
    return (
        f"WARNING: Check output is IDENTICAL to iteration {prev_iter}. "
        "You are going in CIRCLES. Your previous approach did NOT work — "
        "you MUST try a fundamentally DIFFERENT strategy."
    )


def large_file_warning_intro() -> str:
    """Return the introduction for large file warnings."""
    return (
        "CRITICAL WARNING: The following files are >2000 lines and "
        "will be TRUNCATED by the 'read' tool:"
    )


def large_file_warning_item(filepath: str, line_count: int) -> str:
    """Return a single large file warning item.

    Args:
        filepath: Path to the large file
        line_count: Number of lines in the file

    Returns:
        Warning item string

    """
    return f"- {filepath} ({line_count} lines)"


def large_file_warning_critical() -> str:
    """Return the critical warning about flying blind with large files."""
    return (
        "[CRITICAL]: You CANNOT see the bottom of these files. If errors "
        "occur there, you are flying blind."
    )


def large_file_warning_recommendations() -> str:
    """Return recommendations for fixing large files."""
    return (
        "STRONGLY RECOMMENDED: Split these files into smaller files or "
        "modules to bring them under the 2000-line limit.\n"
        "  - If the file contains tests at the bottom, move them to a "
        "separate test file (e.g., tests.rs, test_file.py, file.test.js).\n"
        "  - If it is a large logic file, extract cohesive functionality "
        "into separate source files or subfolders."
    )


def build_large_file_warning(files: Iterable[tuple[str, int]]) -> str:
    """Build complete large file warning from file list.

    Args:
        files: Iterable of (filepath, line_count) tuples

    Returns:
        Complete warning message with intro, items, and recommendations

    """
    if not files:
        return ""

    parts = [large_file_warning_intro()]
    parts.extend(large_file_warning_item(fp, lc) for fp, lc in files)
    parts.extend(["", large_file_warning_critical(), large_file_warning_recommendations()])

    return "\n".join(parts)


def model_recommendations_header() -> str:
    """Return the header for model recommendations."""
    return "RECOMMENDATION: Try a different model:"


def model_recommendation_items() -> str:
    """Return the list of recommended models."""
    return (
        "  - anthropic/claude-sonnet-4-5 (recommended for code editing)\n"
        "  - anthropic/claude-opus-4-6 (high capacity, more expensive)\n"
        "  - github-copilot/gpt-5.2-codex (good for code generation)"
    )


def model_recommendations_full() -> str:
    """Return complete model recommendations message."""
    return f"{model_recommendations_header()}\n{model_recommendation_items()}"


def pr_threads_unsafe_count_warning(unsafe_count: int, unsafe_ids: list[str]) -> str:
    """Return warning about PR threads not in scope.

    Args:
        unsafe_count: Number of unsafe threads
        unsafe_ids: List of unsafe thread IDs

    Returns:
        Warning message

    """
    ids_str = ", ".join(unsafe_ids)
    return f"WARNING: Model reported {unsafe_count} thread(s) NOT in scope: {ids_str}"


def pr_threads_safe_only_message(safe_count: int) -> str:
    """Return message about only resolving safe threads.

    Args:
        safe_count: Number of safe/in-scope threads

    Returns:
        Informational message

    """
    return f"Only resolving the {safe_count} in-scope thread(s)."
