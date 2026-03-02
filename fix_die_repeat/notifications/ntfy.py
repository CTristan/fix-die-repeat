"""ntfy notification backend."""

import logging
import re

from fix_die_repeat.notifications.base import EventType, NotificationEvent, Notifier
from fix_die_repeat.utils import run_command

# ntfy-specific constants
CURL_MAX_TIME_SECONDS = 10
SUBPROCESS_TIMEOUT_SECONDS = 15.0


def sanitize_ntfy_topic(text: str) -> str:
    """Sanitize text for ntfy topic name.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized topic name

    """
    # ntfy allows alphanumeric, hyphen, underscore, and dot
    return re.sub(r"[^a-z0-9._-]", "-", text.lower()).strip("-")


class NtfyNotifier(Notifier):
    """Notification backend for ntfy."""

    def __init__(
        self,
        *,
        enabled: bool,
        url: str,
        logger: logging.Logger,
    ) -> None:
        """Initialize the ntfy notifier.

        Args:
            enabled: Whether ntfy notifications are enabled
            url: ntfy server URL
            logger: Logger instance for debug output

        """
        self.enabled = enabled
        self.url = url
        self.logger = logger

    def is_enabled(self) -> bool:
        """Check if this notifier is enabled.

        Returns:
            True if enabled

        """
        return self.enabled

    def send(self, event: NotificationEvent) -> None:
        """Send a notification to ntfy.

        Args:
            event: The notification event to send

        """
        # Check if curl is available
        returncode, _, _ = run_command(["which", "curl"], check=False)
        if returncode != 0:
            return

        topic = sanitize_ntfy_topic(event.repo_name)

        # Format based on event type
        if event.event_type == EventType.RUN_COMPLETED:
            title = f"✓ {event.repo_name} completed"
            tags = "white_check_mark,done"
            priority = "default"
        elif event.event_type == EventType.RUN_FAILED:
            title = f"✗ {event.repo_name} failed"
            tags = "warning,x"
            priority = "high"
        else:  # OSCILLATION_DETECTED
            title = f"⚠️ {event.repo_name} oscillation"
            tags = "warning,repeat"
            priority = "high"

        # Build message using iteration and duration details
        # Iteration info: "4/10 iterations"
        iter_info = f"{event.iteration}/{event.max_iters} iterations"
        message = f"{event.message} ({iter_info}) in {event.duration_str} on {event.branch}"

        # Send notification (ignore errors)
        run_command(
            [
                "curl",
                "-sS",
                "-X",
                "POST",
                f"{self.url}/{topic}",
                "-H",
                f"Title: {title}",
                "-H",
                f"Tags: {tags}",
                "-H",
                f"Priority: {priority}",
                "--max-time",
                str(CURL_MAX_TIME_SECONDS),
                "--data-raw",
                message,
            ],
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )

        if self.logger:
            self.logger.debug("Sent ntfy notification to %s/%s", self.url, topic)
