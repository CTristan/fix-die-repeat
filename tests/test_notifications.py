"""Tests for the notification system."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fix_die_repeat.notifications.base import EventType, NotificationEvent
from fix_die_repeat.notifications.manager import NotificationManager
from fix_die_repeat.notifications.ntfy import NtfyNotifier, sanitize_ntfy_topic
from fix_die_repeat.notifications.zulip import (
    ZulipConfig,
    ZulipNotifier,
    _parse_repo_name_from_remote,
    detect_branch_name,
    detect_repo_name,
)
from fix_die_repeat.utils import configure_logger

# Constants for test event values
TEST_ITERATION = 4
TEST_MAX_ITERS = 10
TEST_OSCILLATION_ITERATION = 5
TEST_DEFAULT_ITERATIONS = 10


class TestNotificationEvent:
    """Tests for NotificationEvent dataclass."""

    def test_create_event(self) -> None:
        """Test creating a notification event."""
        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )
        assert event.event_type == EventType.RUN_COMPLETED
        assert event.exit_code == 0
        assert event.duration_str == "5m 30s"
        assert event.repo_name == "test-repo"
        assert event.branch == "main"
        assert event.iteration == TEST_ITERATION
        assert event.max_iters == TEST_MAX_ITERS
        assert event.message == "Test message"

    def test_event_immutability(self) -> None:
        """Test that NotificationEvent is immutable (frozen dataclass)."""
        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )
        with pytest.raises(AttributeError):  # frozen dataclass
            event.exit_code = 1  # type: ignore[misc]


class TestNotificationManager:
    """Tests for NotificationManager."""

    def test_dispatch_to_enabled_notifiers(self, tmp_path: Path) -> None:
        """Test that manager dispatches to all enabled notifiers."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        # Create mock notifiers
        notifier1 = MagicMock(spec=NtfyNotifier)
        notifier1.is_enabled.return_value = True
        notifier2 = MagicMock(spec=NtfyNotifier)
        notifier2.is_enabled.return_value = True

        manager = NotificationManager([notifier1, notifier2], logger)

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("threading.Thread") as mock_thread:
            # Make thread execution synchronous for testing
            mock_thread.side_effect = lambda target, args, **_kwargs: MagicMock(
                start=lambda: target(*args)
            )
            manager.notify(event)

        # Both notifiers should have been called
        notifier1.send.assert_called_once_with(event)
        notifier2.send.assert_called_once_with(event)

    def test_skips_disabled_notifiers(self, tmp_path: Path) -> None:
        """Test that manager skips disabled notifiers."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        # Create mock notifiers
        notifier1 = MagicMock(spec=NtfyNotifier)
        notifier1.is_enabled.return_value = True
        notifier2 = MagicMock(spec=NtfyNotifier)
        notifier2.is_enabled.return_value = False

        manager = NotificationManager([notifier1, notifier2], logger)

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("threading.Thread") as mock_thread:
            # Make thread execution synchronous for testing
            mock_thread.side_effect = lambda target, args, **_kwargs: MagicMock(
                start=lambda: target(*args)
            )
            manager.notify(event)

        # Only enabled notifier should have been called
        notifier1.send.assert_called_once_with(event)
        notifier2.send.assert_not_called()

    def test_catches_exceptions_without_raising(self, tmp_path: Path) -> None:
        """Test that manager catches notifier exceptions without raising."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=True)

        # Create mock notifiers
        notifier1 = MagicMock(spec=NtfyNotifier)
        notifier1.is_enabled.return_value = True
        notifier1.send.side_effect = Exception("Test error")
        notifier2 = MagicMock(spec=NtfyNotifier)
        notifier2.is_enabled.return_value = True

        manager = NotificationManager([notifier1, notifier2], logger)

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("threading.Thread") as mock_thread:
            # Make thread execution synchronous for testing
            mock_thread.side_effect = lambda target, args, **_kwargs: MagicMock(
                start=lambda: target(*args)
            )
            # Should not raise
            manager.notify(event)

        # Second notifier should still have been called
        notifier2.send.assert_called_once_with(event)

    def test_no_op_with_empty_notifiers(self, tmp_path: Path) -> None:
        """Test that manager is a no-op with no notifiers."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        manager = NotificationManager([], logger)

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        # Should not raise
        manager.notify(event)


class TestNtfyNotifier:
    """Tests for NtfyNotifier."""

    def test_is_enabled(self, tmp_path: Path) -> None:
        """Test is_enabled returns the enabled flag."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = NtfyNotifier(
            enabled=True,
            url="http://localhost:2586",
            logger=logger,
        )
        assert notifier.is_enabled()

        notifier = NtfyNotifier(
            enabled=False,
            url="http://localhost:2586",
            logger=logger,
        )
        assert not notifier.is_enabled()

    def test_send_completed_event(self, tmp_path: Path) -> None:
        """Test sending a RUN_COMPLETED event."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = NtfyNotifier(
            enabled=True,
            url="http://localhost:2586",
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with (
            patch("fix_die_repeat.notifications.ntfy.shutil") as mock_shutil,
            patch("fix_die_repeat.notifications.ntfy.run_command") as mock_run,
        ):
            mock_shutil.which.return_value = "/usr/bin/curl"
            mock_run.return_value = (0, "", "")
            notifier.send(event)

            # Should have called run_command (for the curl POST)
            assert mock_run.called
            # Check that curl POST was called by examining call arguments
            calls = mock_run.call_args_list
            # Only 1 call expected: the curl POST command
            # (shutil.which is mocked, not run via run_command)
            assert len(calls) >= 1
            # Check that curl POST was called with --data-raw
            has_data_raw = any(
                len(call[0]) > 0 and isinstance(call[0][0], list) and "--data-raw" in call[0][0]
                for call in calls
            )
            assert has_data_raw

    def test_send_failed_event(self, tmp_path: Path) -> None:
        """Test sending a RUN_FAILED event."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = NtfyNotifier(
            enabled=True,
            url="http://localhost:2586",
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_FAILED,
            exit_code=1,
            duration_str="1m 0s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_DEFAULT_ITERATIONS,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("fix_die_repeat.notifications.ntfy.run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            notifier.send(event)

            # Should have been called
            assert mock_run.called

    def test_send_oscillation_event(self, tmp_path: Path) -> None:
        """Test sending an OSCILLATION_DETECTED event."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = NtfyNotifier(
            enabled=True,
            url="http://localhost:2586",
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.OSCILLATION_DETECTED,
            exit_code=0,
            duration_str="3m 15s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_OSCILLATION_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Oscillation detected",
        )

        with patch("fix_die_repeat.notifications.ntfy.run_command") as mock_run:
            mock_run.return_value = (0, "", "")
            notifier.send(event)

            # Should have been called
            assert mock_run.called

    def test_send_without_curl(self, tmp_path: Path) -> None:
        """Test that send returns early when curl is not available."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = NtfyNotifier(
            enabled=True,
            url="http://localhost:2586",
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("fix_die_repeat.notifications.ntfy.run_command") as mock_run:
            # Mock curl check to fail
            mock_run.return_value = (127, "", "")
            notifier.send(event)

            # Only 'which curl' should have been called
            assert mock_run.call_count == 1


class TestSanitizeNtfyTopic:
    """Tests for sanitize_ntfy_topic function."""

    def test_alphanumeric(self) -> None:
        """Test sanitizing alphanumeric string."""
        assert sanitize_ntfy_topic("TestRepo123") == "testrepo123"

    def test_special_chars(self) -> None:
        """Test sanitizing string with special characters."""
        assert sanitize_ntfy_topic("test-repo_name") == "test-repo_name"
        assert sanitize_ntfy_topic("test.repo") == "test.repo"

    def test_spaces_and_specials(self) -> None:
        """Test sanitizing string with spaces and other specials."""
        assert sanitize_ntfy_topic("My Test Repo!") == "my-test-repo"
        assert sanitize_ntfy_topic("test@repo#123") == "test-repo-123"


class TestZulipNotifier:
    """Tests for ZulipNotifier."""

    def test_is_enabled_with_all_fields(self, tmp_path: Path) -> None:
        """Test is_enabled returns True when all fields are present."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="https://zulip.example.com",
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )
        assert notifier.is_enabled()

    def test_is_enabled_when_disabled(self, tmp_path: Path) -> None:
        """Test is_enabled returns False when disabled."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=False,
                server_url="https://zulip.example.com",
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )
        assert not notifier.is_enabled()

    def test_is_enabled_missing_server_url(self, tmp_path: Path) -> None:
        """Test is_enabled returns False when server_url is None."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url=None,
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )
        assert not notifier.is_enabled()

    def test_is_enabled_missing_bot_email(self, tmp_path: Path) -> None:
        """Test is_enabled returns False when bot_email is None."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="https://zulip.example.com",
                bot_email=None,
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )
        assert not notifier.is_enabled()

    def test_is_enabled_missing_bot_api_key(self, tmp_path: Path) -> None:
        """Test is_enabled returns False when bot_api_key is None."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="https://zulip.example.com",
                bot_email="bot@example.com",
                bot_api_key=None,
                stream="fix-die-repeat",
            ),
            logger=logger,
        )
        assert not notifier.is_enabled()

    def test_send_completed_event(self, tmp_path: Path) -> None:
        """Test sending a RUN_COMPLETED event."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="https://zulip.example.com",
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("fix_die_repeat.notifications.zulip.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            notifier.send(event)

            # Verify urlopen was called
            mock_urlopen.assert_called_once()
            call_args = mock_urlopen.call_args
            assert call_args is not None
            request = call_args[0][0]
            assert "zulip.example.com" in request.full_url
            assert request.data
            # Verify message content is present in URL-encoded form
            assert b"content=" in request.data
            assert b"Test+message" in request.data
            assert b"test-repo" in request.data
            assert b"main" in request.data

    def test_send_failed_event(self, tmp_path: Path) -> None:
        """Test sending a RUN_FAILED event."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="https://zulip.example.com",
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_FAILED,
            exit_code=1,
            duration_str="1m 0s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_DEFAULT_ITERATIONS,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("fix_die_repeat.notifications.zulip.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            notifier.send(event)

            # Verify urlopen was called
            mock_urlopen.assert_called_once()
            call_args = mock_urlopen.call_args
            assert call_args is not None
            request = call_args[0][0]
            # Verify message content (event.message) is present in URL-encoded form
            assert b"content=" in request.data
            assert b"Test+message" in request.data

    def test_send_oscillation_event(self, tmp_path: Path) -> None:
        """Test sending an OSCILLATION_DETECTED event."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="https://zulip.example.com",
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.OSCILLATION_DETECTED,
            exit_code=0,
            duration_str="3m 15s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_OSCILLATION_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Oscillation detected",
        )

        with patch("fix_die_repeat.notifications.zulip.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            notifier.send(event)

            # Verify urlopen was called
            mock_urlopen.assert_called_once()
            call_args = mock_urlopen.call_args
            assert call_args is not None
            request = call_args[0][0]
            # Verify message content (event.message) is present in URL-encoded form
            assert b"content=" in request.data
            assert b"Oscillation+detected" in request.data

    def test_send_best_effort_on_network_error(self, tmp_path: Path) -> None:
        """Test that send catches network errors without raising."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="https://zulip.example.com",
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("fix_die_repeat.notifications.zulip.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Network error")

            # Should not raise
            notifier.send(event)

            # Verify urlopen was called
            mock_urlopen.assert_called_once()

    def test_rejects_file_scheme_url(self, tmp_path: Path) -> None:
        """Test that unsafe URL schemes are rejected (S310)."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url="file:///etc/passwd",
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("fix_die_repeat.notifications.zulip.urllib.request.urlopen") as mock_urlopen:
            # Should not call urlopen with unsafe scheme
            notifier.send(event)
            mock_urlopen.assert_not_called()

    def test_rejects_none_server_url(self, tmp_path: Path) -> None:
        """Test that None server URL is rejected."""
        log_file = tmp_path / "test.log"
        logger = configure_logger(fdr_log=log_file, session_log=None, debug=False)

        notifier = ZulipNotifier(
            config=ZulipConfig(
                enabled=True,
                server_url=None,
                bot_email="bot@example.com",
                bot_api_key="test-api-key",
                stream="fix-die-repeat",
            ),
            logger=logger,
        )

        event = NotificationEvent(
            event_type=EventType.RUN_COMPLETED,
            exit_code=0,
            duration_str="5m 30s",
            repo_name="test-repo",
            branch="main",
            iteration=TEST_ITERATION,
            max_iters=TEST_MAX_ITERS,
            message="Test message",
        )

        with patch("fix_die_repeat.notifications.zulip.urllib.request.urlopen") as mock_urlopen:
            # Should not call urlopen with None URL
            notifier.send(event)
            mock_urlopen.assert_not_called()


class TestParseRepoNameFromRemote:
    """Tests for _parse_repo_name_from_remote function."""

    def test_https_url(self) -> None:
        """Test parsing HTTPS remote URL."""
        assert _parse_repo_name_from_remote("https://github.com/user/repo.git") == "repo"
        assert _parse_repo_name_from_remote("https://github.com/user/repo") == "repo"

    def test_ssh_url(self) -> None:
        """Test parsing SSH remote URL."""
        assert _parse_repo_name_from_remote("git@github.com:user/repo.git") == "repo"
        assert _parse_repo_name_from_remote("git@github.com:user/repo") == "repo"

    def test_git_suffix_stripped(self) -> None:
        """Test that .git suffix is stripped."""
        assert _parse_repo_name_from_remote("https://github.com/user/repo.git") == "repo"
        assert _parse_repo_name_from_remote("git@github.com:user/repo.git") == "repo"

    def test_empty_url(self) -> None:
        """Test that empty URL returns None."""
        assert _parse_repo_name_from_remote("") is None
        assert _parse_repo_name_from_remote("   ") is None

    def test_complex_path(self) -> None:
        """Test parsing complex URL path."""
        assert _parse_repo_name_from_remote("https://gitlab.com/group/subgroup/repo.git") == "repo"


class TestDetectRepoName:
    """Tests for detect_repo_name function."""

    def test_uses_git_remote(self, tmp_path: Path) -> None:
        """Test detecting repo name from git remote."""
        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (0, "https://github.com/user/test-repo.git\n", "")
            repo_name = detect_repo_name(tmp_path)
            assert repo_name == "test-repo"

    def test_falls_back_to_directory_name(self, tmp_path: Path) -> None:
        """Test falling back to directory name when git remote fails."""
        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (1, "", "git failed")
            repo_name = detect_repo_name(tmp_path)
            assert repo_name == tmp_path.name

    def test_falls_back_to_unknown_repo(self) -> None:
        """Test falling back to 'unknown-repo' when resolution fails to find a name."""
        mock_path = MagicMock(spec=Path)
        # Mock resolve().name to return an empty string to trigger the fallback
        mock_path.resolve.return_value.name = ""

        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (1, "", "git failed")
            repo_name = detect_repo_name(mock_path)
            assert repo_name == "unknown-repo"

    def test_descriptive_name_for_dot(self) -> None:
        """Test that Path('.') or Path('') results in actual directory name."""
        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (1, "", "git failed")
            # detect_repo_name(Path()) should return CWD name
            repo_name = detect_repo_name(Path())
            assert repo_name == Path.cwd().name


class TestDetectBranchName:
    """Tests for detect_branch_name function."""

    def test_normal_branch(self, tmp_path: Path) -> None:
        """Test detecting normal branch name."""
        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (0, "main\n", "")
            branch = detect_branch_name(tmp_path)
            assert branch == "main"

    def test_detached_head(self, tmp_path: Path) -> None:
        """Test detecting detached HEAD."""
        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (0, "HEAD\n", "")
            branch = detect_branch_name(tmp_path)
            assert branch == "Detached HEAD"

    def test_git_command_failure(self, tmp_path: Path) -> None:
        """Test falling back when git command fails."""
        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (1, "", "git failed")
            branch = detect_branch_name(tmp_path)
            assert branch == "Detached HEAD"

    def test_feature_branch(self, tmp_path: Path) -> None:
        """Test detecting feature branch name."""
        with patch("fix_die_repeat.notifications.zulip.run_command") as mock_run:
            mock_run.return_value = (0, "feature/test-branch\n", "")
            branch = detect_branch_name(tmp_path)
            assert branch == "feature/test-branch"
