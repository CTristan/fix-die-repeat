"""Zulip notification backend."""

import base64
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fix_die_repeat.notifications.base import EventType, NotificationEvent, Notifier
from fix_die_repeat.utils import run_command

# Zulip-specific constants
REQUEST_TIMEOUT = 10.0
ZULIP_MESSAGES_ENDPOINT = "/api/v1/messages"

# Module-level logger for repo detection functions
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZulipConfig:
    """Configuration for Zulip notifications."""

    enabled: bool
    server_url: str | None
    bot_email: str | None
    bot_api_key: str | None
    stream: str


def detect_repo_name(project_root: Path | None = None) -> str:
    """Detect repository name from git remote or fallback to directory name.

    Args:
        project_root: Project root directory (defaults to current working directory)

    Returns:
        Repository name

    """
    # Try to get remote URL
    returncode, stdout, _ = run_command(
        ["git", "remote", "get-url", "origin"],
        check=False,
        cwd=project_root,
    )

    if returncode == 0 and stdout.strip():
        remote_url = stdout.strip()
        parsed = _parse_repo_name_from_remote(remote_url)
        if parsed:
            return parsed

    # Fallback to directory name
    root = (project_root or Path.cwd()).resolve()
    return root.name or "unknown-repo"


def _parse_repo_name_from_remote(remote_url: str) -> str | None:
    """Parse repository name from git remote URL.

    Args:
        remote_url: Git remote URL

    Returns:
        Repository name or None if parsing fails

    """
    trimmed = remote_url.strip()
    if not trimmed:
        return None

    # Remove .git suffix
    without_git = trimmed.removesuffix(".git")

    # Parse HTTPS URLs like https://github.com/user/repo
    if without_git.startswith(("http://", "https://")):
        parts = without_git.replace("http://", "").replace("https://", "").split("/")
        parts = [p for p in parts if p]  # Remove empty strings
        if parts:
            return parts[-1]

    # Parse SSH URLs like git@github.com:user/repo
    if ":" in without_git and "@" in without_git:
        path_part = without_git.split(":")[-1]
        parts = path_part.split("/")
        parts = [p for p in parts if p]
        if parts:
            return parts[-1]

    # Try simple path split
    parts = without_git.split("/")
    parts = [p for p in parts if p]
    if parts:
        return parts[-1]

    return None


def detect_branch_name(project_root: Path | None = None) -> str:
    """Detect current git branch name.

    Args:
        project_root: Project root directory (defaults to current working directory)

    Returns:
        Branch name or fallback string

    """
    returncode, stdout, _ = run_command(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        check=False,
        cwd=project_root,
    )

    if returncode == 0 and stdout.strip():
        branch = stdout.strip()
        if branch and branch != "HEAD":
            return branch

    return "Detached HEAD"


class ZulipNotifier(Notifier):
    """Notification backend for Zulip."""

    def __init__(
        self,
        config: ZulipConfig,
        logger: logging.Logger,
    ) -> None:
        """Initialize the Zulip notifier.

        Args:
            config: Zulip configuration dataclass
            logger: Logger instance for error reporting

        """
        self.config = config
        self.logger = logger

    def is_enabled(self) -> bool:
        """Check if this notifier is enabled and properly configured.

        Returns:
            True if enabled and all required fields are present

        """
        return (
            self.config.enabled
            and bool(self.config.server_url and self.config.server_url.strip())
            and bool(self.config.bot_email and self.config.bot_email.strip())
            and bool(self.config.bot_api_key and self.config.bot_api_key.strip())
        )

    def send(self, event: NotificationEvent) -> None:
        """Send a notification to Zulip.

        Args:
            event: The notification event to send

        """
        # Format message based on event type
        if event.event_type == EventType.RUN_COMPLETED:
            emoji = "✓"
        elif event.event_type == EventType.RUN_FAILED:
            emoji = "✗"
        else:  # OSCILLATION_DETECTED
            emoji = "⚠️"

        # Construct message with iteration info and duration
        iter_info = f"{event.iteration}/{event.max_iters} iterations"
        message = (
            f"{emoji} {event.message} ({iter_info}) "
            f"after {event.duration_str} in **{event.repo_name}** on `{event.branch}`"
        )

        # Validate server URL scheme to prevent security issues (S310)
        if not self.config.server_url or not self.config.server_url.startswith(
            ("http://", "https://"),
        ):
            self.logger.error(
                "Invalid Zulip server URL: must use http:// or https:// (got '%s')",
                self.config.server_url,
            )
            return

        # Build request with proper URL encoding
        url = f"{self.config.server_url.rstrip('/')}{ZULIP_MESSAGES_ENDPOINT}"
        payload = {
            "type": "stream",
            "to": self.config.stream,
            "topic": event.repo_name,
            "content": message,
        }
        data = urllib.parse.urlencode(payload).encode()

        # Basic auth header
        credentials = f"{self.config.bot_email}:{self.config.bot_api_key}"
        auth_header = f"Basic {base64.b64encode(credentials.encode()).decode()}"

        # Validate URL scheme again before use (S310)
        if not url.startswith(("http://", "https://")):
            self.logger.error(
                "Refusing to open URL with unsafe scheme (S310): %s",
                url,
            )
            return

        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        try:
            # Use a 10s timeout to avoid blocking indefinitely
            # URL scheme validated above (S310)
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as _response:
                pass
        except Exception:
            self.logger.exception("Zulip notification failed")
