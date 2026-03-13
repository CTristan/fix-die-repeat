"""Notification configuration file management and validation."""

import base64
import contextlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

# Timeout for validation requests
REQUEST_TIMEOUT = 10.0

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401


class ZulipFileConfig(TypedDict, total=False):
    """Zulip configuration stored in JSON."""

    enabled: bool
    server_url: str
    bot_email: str
    bot_api_key: str
    stream: str


class NtfyFileConfig(TypedDict, total=False):
    """Ntfy configuration stored in JSON."""

    enabled: bool
    url: str


class NotificationFileConfig(TypedDict, total=False):
    """Global notification configuration structure."""

    zulip: ZulipFileConfig
    ntfy: NtfyFileConfig


def get_notification_config_path() -> Path:
    """Return the global notification config file path.

    Returns ~/.config/fix-die-repeat/notifications.json,
    respecting XDG_CONFIG_HOME if set.

    Returns:
        Path to global notification config file

    """
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    config_dir = config_home / "fix-die-repeat"
    return config_dir / "notifications.json"


def load_notification_config(path: Path | None = None) -> NotificationFileConfig:
    """Load notification configuration from JSON.

    Args:
        path: Path to config file (defaults to get_notification_config_path())

    Returns:
        Loaded configuration dictionary, or empty dict if file missing/invalid

    """
    if path is None:
        path = get_notification_config_path()

    if not path.exists():
        return {}

    try:
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return {}
        return json.loads(content)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in %s", path)
        return {}
    except OSError as e:
        logger.warning("Failed to read %s: %s", path, e)
        return {}


def save_notification_config(
    data: NotificationFileConfig,
    path: Path | None = None,
) -> None:
    """Save notification configuration to JSON with secure permissions.

    Args:
        data: Configuration dictionary to save
        path: Path to config file (defaults to get_notification_config_path())

    """
    if path is None:
        path = get_notification_config_path()

    # Ensure parent directory exists with 0o700 permissions
    parent_dir = path.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    try:
        parent_dir.chmod(0o700)
    except OSError as e:
        logger.debug("Could not chmod directory %s: %s", parent_dir, e)

    # Write file with secure permissions applied at creation time
    # Use os.open with mode 0o600 to avoid any window where file has insecure permissions
    content = json.dumps(data, indent=2) + "\n"
    content_bytes = content.encode("utf-8")

    # Create temporary file in the same directory for atomic replacement
    temp_path = parent_dir / f"{path.name}.tmp"
    try:
        # Open with restrictive permissions from the start
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, content_bytes)
        finally:
            os.close(fd)

        # Atomically replace the target file
        temp_path.replace(path)
    except OSError:
        # Clean up temporary file if anything fails
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def validate_zulip_credentials(server_url: str, bot_email: str, bot_api_key: str) -> str:
    """Validate Zulip credentials by fetching the bot's user profile.

    Args:
        server_url: Zulip server base URL
        bot_email: Bot email address
        bot_api_key: Bot API key

    Returns:
        Bot's full name if successful

    Raises:
        ValueError: If validation fails

    """
    if not server_url.startswith(("http://", "https://")):
        msg = "Server URL must start with http:// or https://"
        raise ValueError(msg)

    url = f"{server_url.rstrip('/')}/api/v1/users/me"

    credentials = f"{bot_email}:{bot_api_key}"
    auth_header = f"Basic {base64.b64encode(credentials.encode()).decode()}"

    request = urllib.request.Request(  # noqa: S310
        url,
        headers={"Authorization": auth_header},
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
            if response.status != HTTP_OK:
                msg = f"HTTP {response.status}"
                raise ValueError(msg)  # noqa: TRY301
            data = json.loads(response.read().decode("utf-8"))
            if data.get("result") != "success":
                msg = str(data.get("msg", "Unknown API error"))
                raise ValueError(msg)  # noqa: TRY301
            return str(data.get("full_name", "Zulip Bot"))
    except urllib.error.HTTPError as e:
        if e.code == HTTP_UNAUTHORIZED:
            msg = "Invalid email or API key"
            raise ValueError(msg) from e
        msg = f"HTTP {e.code}: {e.reason}"
        raise ValueError(msg) from e
    except urllib.error.URLError as e:
        msg = f"Network error: {e.reason}"
        raise ValueError(msg) from e
    except Exception as e:
        msg = f"Unexpected error: {e}"
        raise ValueError(msg) from e


def send_zulip_test_notification(
    server_url: str,
    bot_email: str,
    bot_api_key: str,
    stream: str,
) -> None:
    """Send a test notification to a Zulip stream.

    Args:
        server_url: Zulip server base URL
        bot_email: Bot email address
        bot_api_key: Bot API key
        stream: Target stream name

    Raises:
        ValueError: If sending fails

    """
    if not server_url.startswith(("http://", "https://")):
        msg = "Server URL must start with http:// or https://"
        raise ValueError(msg)

    url = f"{server_url.rstrip('/')}/api/v1/messages"
    payload = {
        "type": "stream",
        "to": stream,
        "topic": "fix-die-repeat-test",
        "content": "✅ **fix-die-repeat**: Test notification successful!",
    }
    data = urllib.parse.urlencode(payload).encode()

    credentials = f"{bot_email}:{bot_api_key}"
    auth_header = f"Basic {base64.b64encode(credentials.encode()).decode()}"

    request = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
            if response.status != HTTP_OK:
                msg = f"HTTP {response.status}"
                raise ValueError(msg)  # noqa: TRY301
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code}: {e.reason}"
        raise ValueError(msg) from e
    except Exception as e:
        msg = f"Failed to send test notification: {e}"
        raise ValueError(msg) from e


def send_ntfy_test_notification(url: str, topic: str = "fix-die-repeat-test") -> None:
    """Send a test notification to a ntfy URL.

    Args:
        url: ntfy server URL (base or with embedded topic, e.g.,
             http://localhost:2586 or http://localhost:2586/mytopic)
        topic: ntfy topic name to post to (default: fix-die-repeat-test)
               Ignored if url already contains a path component

    Raises:
        ValueError: If sending fails

    """
    if not url.startswith(("http://", "https://")):
        msg = "URL must start with http:// or https://"
        raise ValueError(msg)

    # Handle both base server URL and full topic URL cases
    # If url already contains a path component (e.g., "http://localhost:2586/test"),
    # use it directly; otherwise append the topic
    base_url = url.rstrip("/")
    post_url = base_url if "/" in base_url.split("://", 1)[-1] else f"{base_url}/{topic}"

    request = urllib.request.Request(  # noqa: S310
        post_url,
        data="✅ fix-die-repeat: Test notification successful!".encode(),
        headers={
            "Title": "fix-die-repeat Test",
            "Tags": "white_check_mark",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310
            if response.status != HTTP_OK:
                msg = f"HTTP {response.status}"
                raise ValueError(msg)  # noqa: TRY301
    except urllib.error.HTTPError as e:
        msg = f"HTTP {e.code}: {e.reason}"
        raise ValueError(msg) from e
    except Exception as e:
        msg = f"Failed to send test notification: {e}"
        raise ValueError(msg) from e
