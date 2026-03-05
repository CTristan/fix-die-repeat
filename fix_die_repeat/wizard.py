"""Interactive configuration wizard for notifications."""

import sys
from typing import cast

import click
from rich.console import Console
from rich.panel import Panel

from fix_die_repeat.detection import is_interactive
from fix_die_repeat.notification_config import (
    NotificationFileConfig,
    NtfyFileConfig,
    ZulipFileConfig,
    load_notification_config,
    save_notification_config,
    send_ntfy_test_notification,
    send_zulip_test_notification,
    validate_zulip_credentials,
)

console = Console()


def _configure_zulip(config: NotificationFileConfig) -> None:
    """Run the Zulip configuration flow.

    Args:
        config: Global notification configuration

    """
    console.print(Panel.fit("Zulip Configuration", style="blue"))
    z: ZulipFileConfig = config.get("zulip", {})

    server_url = click.prompt(
        "Zulip server URL (e.g., https://your-domain.zulipchat.com)",
        default=z.get("server_url", ""),
    )
    bot_email = click.prompt(
        "Bot email address",
        default=z.get("bot_email", ""),
    )

    # Don't show existing API key in prompt for security, just indicate it exists
    api_key_prompt = "Bot API key"
    if z.get("bot_api_key"):
        api_key_prompt += " (press Enter to keep existing)"
        bot_api_key = click.prompt(api_key_prompt, default="", hide_input=True)
        if not bot_api_key:
            bot_api_key = cast("str", z.get("bot_api_key"))
    else:
        bot_api_key = click.prompt(api_key_prompt, hide_input=True)

    console.print("\nValidating Zulip credentials...")
    try:
        bot_name = validate_zulip_credentials(server_url, bot_email, bot_api_key)
        console.print(f"✅ [green]Success! Authenticated as {bot_name}[/green]")
    except Exception as e:  # noqa: BLE001
        console.print(f"❌ [red]Validation failed: {e}[/red]")
        if not click.confirm("Do you want to save anyway?"):
            console.print("Configuration aborted.")
            return

    stream = click.prompt("Default stream name", default=z.get("stream", "fix-die-repeat"))
    enabled = click.confirm("Enable Zulip notifications?", default=z.get("enabled", True))

    z.update(
        {
            "server_url": server_url,
            "bot_email": bot_email,
            "bot_api_key": bot_api_key,
            "stream": stream,
            "enabled": enabled,
        }
    )
    config["zulip"] = z
    save_notification_config(config)
    console.print("✅ [green]Zulip configuration saved.[/green]")

    if click.confirm("Send a test notification now?", default=True):
        try:
            send_zulip_test_notification(server_url, bot_email, bot_api_key, stream)
            console.print("✅ [green]Test notification sent successfully![/green]")
        except Exception as e:  # noqa: BLE001
            console.print(f"❌ [red]Failed to send test notification: {e}[/red]")


def _configure_ntfy(config: NotificationFileConfig) -> None:
    """Run the ntfy configuration flow.

    Args:
        config: Global notification configuration

    """
    console.print(Panel.fit("ntfy Configuration", style="blue"))
    n: NtfyFileConfig = config.get("ntfy", {})

    url = click.prompt(
        "ntfy topic URL (e.g., http://localhost:2586/mytopic)",
        default=n.get("url", "http://localhost:2586"),
    )
    enabled = click.confirm("Enable ntfy notifications?", default=n.get("enabled", True))

    n.update(
        {
            "url": url,
            "enabled": enabled,
        }
    )
    config["ntfy"] = n
    save_notification_config(config)
    console.print("✅ [green]ntfy configuration saved.[/green]")

    if click.confirm("Send a test notification now?", default=True):
        try:
            send_ntfy_test_notification(url)
            console.print("✅ [green]Test notification sent successfully![/green]")
        except Exception as e:  # noqa: BLE001
            console.print(f"❌ [red]Failed to send test notification: {e}[/red]")


def run_wizard() -> None:
    """Run the interactive configuration wizard."""
    if not is_interactive():
        console.print("[red]Error: Wizard must be run in an interactive terminal.[/red]")
        sys.exit(1)

    console.print(Panel.fit("Fix. Die. Repeat. - Notification Configuration", style="cyan"))
    config = load_notification_config()

    while True:
        console.print("\n[bold]Main Menu[/bold]")
        console.print("1. Configure Zulip")
        console.print("2. Configure ntfy")
        console.print("3. Done")

        try:
            choice = click.prompt("Select an option", type=click.Choice(["1", "2", "3"]))

            if choice == "1":
                _configure_zulip(config)
            elif choice == "2":
                _configure_ntfy(config)
            elif choice == "3":
                console.print("Exiting configuration wizard.")
                break
        except click.Abort:
            console.print("\nAborted.")
            break
        except KeyboardInterrupt:
            console.print("\nInterrupted.")
            break
