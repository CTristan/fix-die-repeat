"""Notification system for fix-die-repeat.

This package provides an extensible notification architecture with
multiple backend support (ntfy, Zulip, and future backends).
"""

from fix_die_repeat.notifications.base import (
    EventType,
    NotificationEvent,
    Notifier,
)
from fix_die_repeat.notifications.manager import NotificationManager
from fix_die_repeat.notifications.ntfy import NtfyNotifier
from fix_die_repeat.notifications.zulip import (
    ZulipConfig,
    ZulipNotifier,
    detect_branch_name,
    detect_repo_name,
)

__all__ = [
    "EventType",
    "NotificationEvent",
    "NotificationManager",
    "Notifier",
    "NtfyNotifier",
    "ZulipConfig",
    "ZulipNotifier",
    "detect_branch_name",
    "detect_repo_name",
]
