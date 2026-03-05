"""Tests for the interactive configuration wizard."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from fix_die_repeat.cli import main
from fix_die_repeat.wizard import run_wizard


@pytest.fixture
def mock_isatty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock is_interactive to return True."""
    monkeypatch.setattr("fix_die_repeat.wizard.is_interactive", lambda: True)


@pytest.fixture
def mock_load_config(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock load_notification_config."""
    mock = MagicMock(return_value={})
    monkeypatch.setattr("fix_die_repeat.wizard.load_notification_config", mock)
    return mock


@pytest.fixture
def mock_save_config(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock save_notification_config."""
    mock = MagicMock()
    monkeypatch.setattr("fix_die_repeat.wizard.save_notification_config", mock)
    return mock


@pytest.fixture
def mock_validate(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock validate_zulip_credentials."""
    mock = MagicMock(return_value="Test Bot")
    monkeypatch.setattr("fix_die_repeat.wizard.validate_zulip_credentials", mock)
    return mock


@pytest.fixture
def mock_send_zulip(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock send_zulip_test_notification."""
    mock = MagicMock()
    monkeypatch.setattr("fix_die_repeat.wizard.send_zulip_test_notification", mock)
    return mock


@pytest.fixture
def mock_send_ntfy(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock send_ntfy_test_notification."""
    mock = MagicMock()
    monkeypatch.setattr("fix_die_repeat.wizard.send_ntfy_test_notification", mock)
    return mock


def test_wizard_not_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test wizard exits if not interactive."""
    monkeypatch.setattr("fix_die_repeat.wizard.is_interactive", lambda: False)
    with pytest.raises(SystemExit):
        run_wizard()


def test_wizard_cli_integration() -> None:
    """Test the config command is registered in the CLI."""
    runner = CliRunner()
    with patch("fix_die_repeat.cli.run_wizard") as mock_run_wizard:
        result = runner.invoke(main, ["config"])
        assert result.exit_code == 0
        mock_run_wizard.assert_called_once()


def test_configure_zulip_success(
    mock_isatty: None,  # noqa: ARG001
    mock_load_config: MagicMock,  # noqa: ARG001
    mock_save_config: MagicMock,
    mock_validate: MagicMock,
    mock_send_zulip: MagicMock,
) -> None:
    """Test successful Zulip configuration flow."""
    # Prompts:
    # 1. Main menu choice: "1" (Zulip)
    # 2. Server URL: "https://z.com"
    # 3. Bot email: "bot@z.com"
    # 4. API key: "key"
    # 5. Stream name: "test-stream"
    # 6. Enable notifications?: "y"
    # 7. Send test notification?: "y"
    # 8. Main menu choice: "3" (Done)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["config"],
        input="1\nhttps://z.com\nbot@z.com\nkey\ntest-stream\ny\ny\n3\n",
    )

    assert result.exit_code == 0
    mock_validate.assert_called_once_with("https://z.com", "bot@z.com", "key")
    mock_save_config.assert_called_once()

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config["zulip"]["server_url"] == "https://z.com"
    assert saved_config["zulip"]["bot_email"] == "bot@z.com"
    assert saved_config["zulip"]["bot_api_key"] == "key"
    assert saved_config["zulip"]["stream"] == "test-stream"
    assert saved_config["zulip"]["enabled"] is True

    mock_send_zulip.assert_called_once_with("https://z.com", "bot@z.com", "key", "test-stream")


def test_configure_ntfy_success(
    mock_isatty: None,  # noqa: ARG001
    mock_load_config: MagicMock,  # noqa: ARG001
    mock_save_config: MagicMock,
    mock_send_ntfy: MagicMock,
) -> None:
    """Test successful ntfy configuration flow."""
    # Prompts:
    # 1. Main menu choice: "2" (ntfy)
    # 2. URL: "http://n.com"
    # 3. Enable notifications?: "y"
    # 4. Send test notification?: "y"
    # 5. Main menu choice: "3" (Done)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["config"],
        input="2\nhttp://n.com\ny\ny\n3\n",
    )

    assert result.exit_code == 0
    mock_save_config.assert_called_once()

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config["ntfy"]["url"] == "http://n.com"
    assert saved_config["ntfy"]["enabled"] is True

    mock_send_ntfy.assert_called_once_with("http://n.com")


def test_configure_zulip_validation_failure_abort(
    mock_isatty: None,  # noqa: ARG001
    mock_load_config: MagicMock,  # noqa: ARG001
    mock_save_config: MagicMock,
    mock_validate: MagicMock,
) -> None:
    """Test Zulip config aborted on validation failure."""
    mock_validate.side_effect = ValueError("Invalid API key")

    # Prompts:
    # 1. Main menu: "1" (Zulip)
    # 2. URL: "https://z.com"
    # 3. Email: "bot@z.com"
    # 4. API key: "key"
    # 5. Save anyway?: "n"
    # 6. Main menu: "3" (Done)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["config"],
        input="1\nhttps://z.com\nbot@z.com\nkey\nn\n3\n",
    )

    assert result.exit_code == 0
    mock_save_config.assert_not_called()
