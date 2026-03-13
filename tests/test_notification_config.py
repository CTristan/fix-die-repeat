"""Tests for notification configuration management."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from fix_die_repeat.notification_config import (
    NotificationFileConfig,
    get_notification_config_path,
    load_notification_config,
    save_notification_config,
    send_ntfy_test_notification,
    send_zulip_test_notification,
    validate_zulip_credentials,
)


def test_get_notification_config_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test default config path resolution."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", "/fake/home")
    path = get_notification_config_path()
    assert path == Path("/fake/home/.config/fix-die-repeat/notifications.json")


def test_get_notification_config_path_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test XDG_CONFIG_HOME config path resolution."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/fake/xdg")
    path = get_notification_config_path()
    assert path == Path("/fake/xdg/fix-die-repeat/notifications.json")


def test_load_notification_config_missing(tmp_path: Path) -> None:
    """Test loading a non-existent config file."""
    config_path = tmp_path / "missing.json"
    result = load_notification_config(config_path)
    assert result == {}


def test_load_notification_config_empty(tmp_path: Path) -> None:
    """Test loading an empty config file."""
    config_path = tmp_path / "empty.json"
    config_path.write_text("")
    result = load_notification_config(config_path)
    assert result == {}


def test_load_notification_config_invalid(tmp_path: Path) -> None:
    """Test loading invalid JSON."""
    config_path = tmp_path / "invalid.json"
    config_path.write_text("not json")
    result = load_notification_config(config_path)
    assert result == {}


def test_load_notification_config_valid(tmp_path: Path) -> None:
    """Test loading valid JSON config."""
    config_path = tmp_path / "valid.json"
    data: NotificationFileConfig = {
        "zulip": {"enabled": True, "server_url": "https://z.com"},
        "ntfy": {"enabled": False, "url": "http://n.com"},
    }
    config_path.write_text(json.dumps(data))
    result = load_notification_config(config_path)
    assert result == data


def test_save_notification_config(tmp_path: Path) -> None:
    """Test saving config file and directory permissions."""
    config_path = tmp_path / "sub" / "config.json"
    data: NotificationFileConfig = {
        "zulip": {"enabled": True, "server_url": "https://z.com"},
        "ntfy": {"enabled": False, "url": "http://n.com"},
    }

    save_notification_config(data, config_path)

    assert config_path.exists()
    assert json.loads(config_path.read_text()) == data


@patch("urllib.request.urlopen")
def test_validate_zulip_credentials_success(mock_urlopen: MagicMock) -> None:
    """Test successful Zulip credential validation."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps(
        {"result": "success", "full_name": "Test Bot"}
    ).encode()
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    name = validate_zulip_credentials("https://zulip.com", "bot@z.com", "key")
    assert name == "Test Bot"

    # Check request
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://zulip.com/api/v1/users/me"


@patch("urllib.request.urlopen")
def test_validate_zulip_credentials_auth_failure(mock_urlopen: MagicMock) -> None:
    """Test Zulip credential validation auth failure."""
    mock_urlopen.side_effect = HTTPError("url", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid email or API key"):
        validate_zulip_credentials("https://zulip.com", "bot@z.com", "key")


@patch("urllib.request.urlopen")
def test_send_zulip_test_notification_success(mock_urlopen: MagicMock) -> None:
    """Test successful Zulip test notification."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    send_zulip_test_notification("https://zulip.com", "bot@z.com", "key", "stream")


@patch("urllib.request.urlopen")
def test_send_ntfy_test_notification_success(mock_urlopen: MagicMock) -> None:
    """Test successful ntfy test notification."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    send_ntfy_test_notification("http://localhost:2586/test")


@patch("urllib.request.urlopen")
def test_validate_zulip_credentials_http_error(mock_urlopen: MagicMock) -> None:
    """Test Zulip credential validation surfaces non-401 HTTP errors."""
    mock_urlopen.side_effect = HTTPError("url", 500, "Internal Server Error", {}, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="HTTP 500: Internal Server Error"):
        validate_zulip_credentials("https://zulip.com", "bot@z.com", "key")


def test_validate_zulip_credentials_invalid_scheme() -> None:
    """Test Zulip credential validation rejects non-http(s) server URLs."""
    with pytest.raises(ValueError, match="Server URL must start with http:// or https://"):
        validate_zulip_credentials("ftp://zulip.com", "bot", "key")


@patch("urllib.request.urlopen")
def test_send_zulip_test_notification_http_error(mock_urlopen: MagicMock) -> None:
    """Test Zulip test notification surfaces HTTP errors."""
    mock_urlopen.side_effect = HTTPError("url", 404, "Not Found", {}, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="HTTP 404: Not Found"):
        send_zulip_test_notification("https://zulip.com", "bot", "key", "stream")


def test_send_zulip_test_notification_invalid_scheme() -> None:
    """Test Zulip test notification rejects non-http(s) server URLs."""
    with pytest.raises(ValueError, match="Server URL must start with http:// or https://"):
        send_zulip_test_notification("ftp://zulip.com", "bot", "key", "stream")


@patch("urllib.request.urlopen")
def test_send_ntfy_test_notification_http_error(mock_urlopen: MagicMock) -> None:
    """Test ntfy test notification surfaces HTTP errors."""
    mock_urlopen.side_effect = HTTPError("url", 400, "Bad Request", {}, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="HTTP 400: Bad Request"):
        send_ntfy_test_notification("http://n.com/t")


def test_send_ntfy_test_notification_invalid_scheme() -> None:
    """Test ntfy test notification rejects non-http(s) URLs."""
    with pytest.raises(ValueError, match="URL must start with http:// or https://"):
        send_ntfy_test_notification("ftp://n.com/t")
