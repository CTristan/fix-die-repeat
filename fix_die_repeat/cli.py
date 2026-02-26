"""Command-line interface for fix-die-repeat."""

import sys
import traceback

import click
from rich.console import Console

from fix_die_repeat.config import CliOptions, Paths, get_settings
from fix_die_repeat.detection import (
    get_system_config_path,
    resolve_check_cmd,
    validate_check_cmd_or_exit,
)
from fix_die_repeat.runner import PiRunner
from fix_die_repeat.utils import is_running_in_dev_mode

console = Console()


@click.command()
@click.option(
    "-c",
    "--check-cmd",
    help="Command to run checks (default: auto-detected)",
    envvar="FDR_CHECK_CMD",
)
@click.option(
    "-n",
    "--max-iters",
    type=int,
    help="Maximum loop iterations (default: 10)",
    envvar="FDR_MAX_ITERS",
)
@click.option(
    "-m",
    "--model",
    help="Override model selection (e.g., anthropic/claude-sonnet-4-5)",
    envvar="FDR_MODEL",
)
@click.option(
    "--max-pr-threads",
    type=int,
    help="Maximum PR threads to process per iteration (default: 5)",
    envvar="FDR_MAX_PR_THREADS",
)
@click.option(
    "--archive-artifacts",
    is_flag=True,
    help="Archive existing artifacts to a timestamped folder",
    envvar="FDR_ARCHIVE_ARTIFACTS",
)
@click.option(
    "--no-compact",
    is_flag=True,
    help="Skip automatic compaction of large artifacts",
)
@click.option(
    "--pr-review",
    is_flag=True,
    help="Enable PR review mode",
    envvar="FDR_PR_REVIEW",
)
@click.option(
    "--test-model",
    help="Test model compatibility before running (exits after test)",
    envvar="FDR_TEST_MODEL",
)
@click.option(
    "-d",
    "--debug",
    is_flag=True,
    help="Enable debug mode (timestamped session logs and verbose logging)",
    envvar="FDR_DEBUG",
)
@click.version_option()
def main(**kwargs: str | int | bool | None) -> None:
    r"""Automated check, review, and fix loop using pi.

    \f
    fix-die-repeat is an automated tool that:
    1. Runs your check command (CI/tests)
    2. If checks fail, uses pi to fix the errors
    3. If checks pass, reviews the changes using pi
    4. If review finds issues, fixes them
    5. Repeats until all checks pass and no issues are found

    Environment variables:
      FDR_CHECK_CMD, FDR_MAX_ITERS, FDR_MODEL, FDR_MAX_PR_THREADS,
      FDR_ARCHIVE_ARTIFACTS, FDR_COMPACT_ARTIFACTS, FDR_PR_REVIEW, FDR_DEBUG,
      FDR_NTFY_ENABLED (default: 1), FDR_NTFY_URL (default: http://localhost:2586)

    Examples:
      # Run with default settings
      fix-die-repeat

      # Use a custom check command
      fix-die-repeat -c "make test"

      # Test a model before running
      fix-die-repeat --test-model anthropic/claude-sonnet-4-5

      # Enable PR review mode
      fix-die-repeat --pr-review

    """
    debug = bool(kwargs.get("debug", False))
    try:
        options = _build_cli_options(kwargs)
        _run_main(options)

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        if debug:
            console.print(traceback.format_exc())
        sys.exit(1)


def _build_cli_options(kwargs: dict[str, str | int | bool | None]) -> CliOptions:
    """Build CliOptions from Click's keyword arguments.

    Click passes each @click.option value as a keyword argument. This
    function maps them into the CliOptions dataclass so downstream code
    works with a typed object instead of a raw dict.

    Click guarantees value types via each option's ``type=`` parameter,
    so the casts below are safe.

    Args:
        kwargs: Keyword arguments injected by Click decorators

    Returns:
        CliOptions with CLI-provided overrides

    """
    check_cmd = kwargs.get("check_cmd")
    max_iters = kwargs.get("max_iters")
    model = kwargs.get("model")
    max_pr_threads = kwargs.get("max_pr_threads")
    test_model = kwargs.get("test_model")
    archive_flag = kwargs.get("archive_artifacts")

    return CliOptions(
        check_cmd=str(check_cmd) if check_cmd is not None else None,
        max_iters=int(max_iters) if max_iters is not None else None,
        model=str(model) if model is not None else None,
        max_pr_threads=int(max_pr_threads) if max_pr_threads is not None else None,
        archive_artifacts=bool(archive_flag) if archive_flag else None,
        no_compact=bool(kwargs.get("no_compact", False)),
        pr_review=bool(kwargs.get("pr_review", False)),
        test_model=str(test_model) if test_model is not None else None,
        debug=bool(kwargs.get("debug", False)),
    )


def _run_main(options: CliOptions) -> int:
    """Run main application logic using CliOptions.

    Avoids Click's parameter explosion by accepting a grouped options
    object instead of individual parameters.

    Args:
        options: CLI options grouped into a dataclass

    Returns:
        Exit code from PiRunner

    """
    # Show dev mode indicator if running from editable install
    if is_running_in_dev_mode():
        console.print("[cyan]⚡ Running in DEV mode (editable install)[/cyan]")

    # Get settings
    settings = get_settings(options)

    # Initialize paths
    paths = Paths()

    # Resolve check command if not provided via CLI/env
    if settings.check_cmd is None:
        settings.check_cmd = resolve_check_cmd(
            cli_check_cmd=options.check_cmd,
            project_config_path=paths.config_file,
            system_config_path=get_system_config_path(),
            project_root=str(paths.project_root),
        )

    # Ensure we have a concrete check command before validation
    if settings.check_cmd is None:
        console.print(
            "[red]Error:[/red] Unable to determine a check command to run.\n"
            "Please specify one via the [bold]--check-cmd[/bold] option or the "
            "[bold]FDR_CHECK_CMD[/bold] environment variable."
        )
        raise SystemExit(1)

    # Pre-flight validation of resolved check command
    validate_check_cmd_or_exit(settings.check_cmd)

    # Create runner
    runner = PiRunner(settings, paths)

    # Run the loop
    return runner.run()


if __name__ == "__main__":
    main()
