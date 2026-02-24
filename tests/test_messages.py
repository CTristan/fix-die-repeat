"""Tests for messages module."""

from fix_die_repeat.messages import (
    build_large_file_warning,
    git_checkout_instructions,
    git_diff_instructions,
    model_recommendations_full,
    oscillation_warning,
    pr_threads_safe_only_message,
    pr_threads_unsafe_count_warning,
)
from fix_die_repeat.utils import format_duration


class TestFormatDuration:
    """Tests for format_duration function."""

    def test_format_duration_seconds_only(self) -> None:
        """Test formatting with only seconds."""
        assert format_duration(0) == "0s"
        assert format_duration(1) == "1s"
        assert format_duration(30) == "30s"
        assert format_duration(59) == "59s"

    def test_format_duration_minutes_and_seconds(self) -> None:
        """Test formatting with minutes and seconds."""
        assert format_duration(60) == "1m 0s"
        assert format_duration(90) == "1m 30s"
        assert format_duration(125) == "2m 5s"
        assert format_duration(3599) == "59m 59s"

    def test_format_duration_hours_minutes_seconds(self) -> None:
        """Test formatting with hours, minutes, and seconds."""
        assert format_duration(3600) == "1h 0m 0s"
        assert format_duration(3661) == "1h 1m 1s"
        assert format_duration(7200) == "2h 0m 0s"
        assert format_duration(7265) == "2h 1m 5s"

    def test_format_duration_large_values(self) -> None:
        """Test formatting with large duration values."""
        assert format_duration(86400) == "24h 0m 0s"  # 1 day
        assert format_duration(90061) == "25h 1m 1s"


class TestOscillationWarning:
    """Tests for oscillation_warning function."""

    def test_oscillation_warning_message(self) -> None:
        """Test that oscillation warning contains expected information."""
        msg = oscillation_warning(5)
        assert "5" in msg
        assert "oscillation" in msg.lower() or "circle" in msg.lower()

    def test_oscillation_warning_different_iterations(self) -> None:
        """Test oscillation warning for different iteration numbers."""
        msg1 = oscillation_warning(1)
        msg2 = oscillation_warning(3)
        msg3 = oscillation_warning(10)
        assert "1" in msg1
        assert "3" in msg2
        assert "10" in msg3


class TestGitDiffInstructions:
    """Tests for git_diff_instructions function."""

    def test_git_diff_instructions_contains_sha(self) -> None:
        """Test that git diff instructions include the provided SHA."""
        sha = "abc123def456"
        msg = git_diff_instructions(sha)
        assert sha in msg
        assert "diff" in msg.lower()

    def test_git_diff_instructions_format(self) -> None:
        """Test that git diff instructions have proper format."""
        sha = "test-sha-123"
        msg = git_diff_instructions(sha)
        # Should contain git command
        assert "git" in msg.lower()


class TestGitCheckoutInstructions:
    """Tests for git_checkout_instructions function."""

    def test_git_checkout_instructions_contains_sha(self) -> None:
        """Test that git checkout instructions include the provided SHA."""
        sha = "abc123def456"
        msg = git_checkout_instructions(sha)
        assert sha in msg
        assert "checkout" in msg.lower()

    def test_git_checkout_instructions_format(self) -> None:
        """Test that git checkout instructions have proper format."""
        sha = "test-sha-123"
        msg = git_checkout_instructions(sha)
        # Should contain git command
        assert "git" in msg.lower()


class TestBuildLargeFileWarning:
    """Tests for build_large_file_warning function."""

    def test_empty_large_files_list(self) -> None:
        """Test with no large files."""
        msg = build_large_file_warning([])
        assert msg == ""

    def test_single_large_file(self) -> None:
        """Test with a single large file."""
        msg = build_large_file_warning([("file1.py", 2500)])
        assert "file1.py" in msg
        assert "2500" in msg

    def test_multiple_large_files(self) -> None:
        """Test with multiple large files."""
        msg = build_large_file_warning([("file1.py", 2500), ("file2.py", 3000)])
        assert "file1.py" in msg
        assert "file2.py" in msg
        assert "2500" in msg
        assert "3000" in msg

    def test_large_file_warning_content(self) -> None:
        """Test that large file warning contains helpful information."""
        msg = build_large_file_warning([("big.py", 5000)])
        assert "large" in msg.lower() or "threshold" in msg.lower()


class TestModelRecommendationsFull:
    """Tests for model_recommendations_full function."""

    def test_model_recommendations_returns_string(self) -> None:
        """Test that model recommendations returns a string."""
        msg = model_recommendations_full()
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_model_recommendations_content(self) -> None:
        """Test that model recommendations contain expected content."""
        msg = model_recommendations_full()
        # Should mention models
        assert "model" in msg.lower()
        # Should be multi-line
        assert "\n" in msg


class TestPRThreadsUnsafeCountWarning:
    """Tests for pr_threads_unsafe_count_warning function."""

    def test_pr_threads_unsafe_warning_single(self) -> None:
        """Test warning for a single unsafe thread."""
        msg = pr_threads_unsafe_count_warning(1, ["thread1"])
        assert "1" in msg
        assert "not in scope" in msg.lower()

    def test_pr_threads_unsafe_warning_multiple(self) -> None:
        """Test warning for multiple unsafe threads."""
        msg = pr_threads_unsafe_count_warning(3, ["thread1", "thread2", "thread3"])
        assert "3" in msg
        assert "not in scope" in msg.lower()

    def test_pr_threads_unsafe_warning_includes_ids(self) -> None:
        """Test that warning includes thread IDs."""
        thread_ids = ["id1", "id2"]
        msg = pr_threads_unsafe_count_warning(2, thread_ids)
        for thread_id in thread_ids:
            assert thread_id in msg


class TestPRThreadsSafeOnlyMessage:
    """Tests for pr_threads_safe_only_message function."""

    def test_pr_threads_safe_only_single(self) -> None:
        """Test message for a single safe thread."""
        msg = pr_threads_safe_only_message(1)
        assert "1" in msg
        assert "in-scope" in msg.lower()

    def test_pr_threads_safe_only_multiple(self) -> None:
        """Test message for multiple safe threads."""
        msg = pr_threads_safe_only_message(5)
        assert "5" in msg
        assert "in-scope" in msg.lower()

    def test_pr_threads_safe_only_zero(self) -> None:
        """Test message for zero safe threads."""
        msg = pr_threads_safe_only_message(0)
        assert "0" in msg
        assert "in-scope" in msg.lower()
