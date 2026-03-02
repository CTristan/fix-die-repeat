"""Base abstractions for the notification system."""

from dataclasses import dataclass
from enum import StrEnum


class EventType(StrEnum):
    """Types of notification events."""

    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    OSCILLATION_DETECTED = "oscillation_detected"


@dataclass(frozen=True)
class NotificationEvent:
    """Data class representing a notification event.

    Attributes:
        event_type: The type of event
        exit_code: Process exit code
        duration_str: Human-readable duration string
        repo_name: Repository name
        branch: Git branch name
        iteration: Current iteration number
        max_iters: Maximum iterations allowed
        message: Human-readable summary message

    """

    event_type: EventType
    exit_code: int
    duration_str: str
    repo_name: str
    branch: str
    iteration: int
    max_iters: int
    message: str


class Notifier:
    """Protocol for notification backends.

    All notification backends must implement this protocol.
    """

    def send(self, event: NotificationEvent) -> None:
        """Send a notification for the given event.

        Args:
            event: The notification event to send

        """
        raise NotImplementedError

    def is_enabled(self) -> bool:
        """Check if this notifier is enabled and configured.

        Returns:
            True if enabled and ready to send notifications

        """
        raise NotImplementedError
