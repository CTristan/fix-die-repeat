"""Notification manager for dispatching events to multiple backends."""

import logging
import threading

from fix_die_repeat.notifications.base import NotificationEvent, Notifier


class NotificationManager:
    """Manages multiple notification backends and dispatches events.

    The manager collects all registered notifiers and dispatches events
    to every enabled one, catching and logging exceptions per-backend.
    This is a best-effort system: notification failures never block or
    crash the main fix loop.
    """

    def __init__(self, notifiers: list[Notifier], logger: logging.Logger) -> None:
        """Initialize the notification manager.

        Args:
            notifiers: List of notifier instances to dispatch events to
            logger: Logger instance for error reporting

        """
        self.notifiers = notifiers
        self.logger = logger
        self._threads: list[threading.Thread] = []

    def notify(self, event: NotificationEvent) -> None:
        """Dispatch an event to all enabled notifiers in background threads.

        Args:
            event: The notification event to dispatch

        """
        if not self.notifiers:
            return

        # Clean up finished threads
        self._threads = [t for t in self._threads if t.is_alive()]

        for notifier in self.notifiers:
            if not notifier.is_enabled():
                continue

            # Dispatch in a daemon thread so it doesn't block the main fix loop
            # and allows the process to exit even if a notification is pending.
            # We explicitly wait() for these at the end of the run.
            thread = threading.Thread(
                target=self._send_safe,
                args=(notifier, event),
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def wait(self, timeout: float = 10.0) -> None:
        """Wait for all pending notifications to complete.

        Args:
            timeout: Maximum seconds to wait per thread (best-effort)

        """
        if not self._threads:
            return

        active_threads = [t for t in self._threads if t.is_alive()]
        if not active_threads:
            return

        self.logger.info("Waiting for %s pending notification(s)...", len(active_threads))
        for thread in active_threads:
            thread.join(timeout=timeout)

        # Final cleanup
        self._threads = [t for t in self._threads if t.is_alive()]

    def _send_safe(self, notifier: Notifier, event: NotificationEvent) -> None:
        """Safely send a notification, catching and logging all exceptions.

        Args:
            notifier: Notifier instance to use
            event: Notification event to send

        """
        try:
            notifier.send(event)
        except Exception:
            self.logger.exception("Notification failed")
