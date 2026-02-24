"""Tests for message generators in messages.py."""

from fix_die_repeat.messages import (
    build_large_file_warning,
    git_checkout_instructions,
    git_diff_instructions,
    large_file_warning_critical,
    large_file_warning_intro,
    large_file_warning_item,
    large_file_warning_recommendations,
    model_recommendation_items,
    model_recommendations_full,
    model_recommendations_header,
    oscillation_warning,
    pr_threads_safe_only_message,
    pr_threads_unsafe_count_warning,
)


class TestGitInstructions:
    """Tests for git instruction messages."""

    def test_git_diff_instructions(self) -> None:
        """Test git diff instruction generation."""
        result = git_diff_instructions("abc123")
        assert result == "To see all changes made: git diff abc123"

    def test_git_checkout_instructions(self) -> None:
        """Test git checkout instruction generation."""
        result = git_checkout_instructions("abc123")
        assert result == "To revert all changes:   git checkout abc123 -- ."


class TestOscillationWarning:
    """Tests for oscillation warning messages."""

    def test_oscillation_warning_basic(self) -> None:
        """Test oscillation warning with previous iteration."""
        result = oscillation_warning(3)
        assert "IDENTICAL to iteration 3" in result
        assert "CIRCLES" in result
        assert "DIFFERENT strategy" in result


class TestLargeFileWarning:
    """Tests for large file warning messages."""

    def test_large_file_warning_intro(self) -> None:
        """Test large file warning intro."""
        result = large_file_warning_intro()
        assert "CRITICAL WARNING" in result
        assert ">2000 lines" in result
        assert "TRUNCATED" in result

    def test_large_file_warning_item(self) -> None:
        """Test large file warning item."""
        result = large_file_warning_item("src/main.py", 2500)
        assert result == "- src/main.py (2500 lines)"

    def test_large_file_warning_critical(self) -> None:
        """Test large file critical warning."""
        result = large_file_warning_critical()
        assert "CRITICAL" in result
        assert "CANNOT see the bottom" in result
        assert "flying blind" in result

    def test_large_file_warning_recommendations(self) -> None:
        """Test large file recommendations."""
        result = large_file_warning_recommendations()
        assert "STRONGLY RECOMMENDED" in result
        assert "2000-line limit" in result
        assert "separate test file" in result
        assert "separate source files" in result

    def test_build_large_file_warning_empty(self) -> None:
        """Test building large file warning with no files."""
        result = build_large_file_warning([])
        assert result == ""

    def test_build_large_file_warning_single(self) -> None:
        """Test building large file warning with one file."""
        files = [("src/main.py", 2100)]
        result = build_large_file_warning(files)
        assert "CRITICAL WARNING" in result
        assert "src/main.py (2100 lines)" in result
        assert "CRITICAL" in result
        assert "STRONGLY RECOMMENDED" in result

    def test_build_large_file_warning_multiple(self) -> None:
        """Test building large file warning with multiple files."""
        files = [("src/main.py", 2100), ("tests/test_main.py", 2500)]
        result = build_large_file_warning(files)
        assert "src/main.py (2100 lines)" in result
        assert "tests/test_main.py (2500 lines)" in result
        # Verify the warning has proper structure
        lines = result.split("\n")
        assert "- src/main.py (2100 lines)" in lines
        assert "- tests/test_main.py (2500 lines)" in lines


class TestModelRecommendations:
    """Tests for model recommendation messages."""

    def test_model_recommendations_header(self) -> None:
        """Test model recommendations header."""
        result = model_recommendations_header()
        assert result == "RECOMMENDATION: Try a different model:"

    def test_model_recommendation_items(self) -> None:
        """Test model recommendation items."""
        result = model_recommendation_items()
        assert "anthropic/claude-sonnet-4-5" in result
        assert "anthropic/claude-opus-4-6" in result
        assert "github-copilot/gpt-5.2-codex" in result
        assert "recommended for code editing" in result

    def test_model_recommendations_full(self) -> None:
        """Test complete model recommendations message."""
        result = model_recommendations_full()
        assert "RECOMMENDATION: Try a different model:" in result
        assert "anthropic/claude-sonnet-4-5" in result


class TestPRThreadWarnings:
    """Tests for PR thread warning messages."""

    def test_pr_threads_unsafe_count_warning(self) -> None:
        """Test PR thread unsafe count warning."""
        result = pr_threads_unsafe_count_warning(2, ["id1", "id2"])
        assert "2 thread(s) NOT in scope" in result
        assert "id1, id2" in result
        assert "WARNING" in result

    def test_pr_threads_safe_only_message(self) -> None:
        """Test PR thread safe-only message."""
        result = pr_threads_safe_only_message(3)
        assert "3 in-scope thread(s)" in result
        assert "Only resolving" in result
