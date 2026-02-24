"""Command-line interface for fix-die-repeat."""

import sys
import traceback

import click
from rich.console import Console

from fix_die_repeat.config import CliOptions, Paths, get_settings
from fix_die_repeat.runner import PiRunner

console = Console()


@click.command()
@click.option(
    "-c",
    "--check-cmd",
    help="Command to run checks (default: ./scripts/ci.sh)",
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
def main(
    check_cmd: str | None,
    max_iters: int | None,
    model: str | None,
    max_pr_threads: int | None,
    archive_artifacts: bool,
    no_compact: bool,
    pr_review: bool,
    test_model: str | None,
    debug: bool,
) -> None:
    """Automated check, review, and fix loop using pi.

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
    try:
        # Create CLI options object
        options = CliOptions(
            check_cmd=check_cmd,
            max_iters=max_iters,
            model=model,
            max_pr_threads=max_pr_threads,
            archive_artifacts=archive_artifacts or None,
            no_compact=no_compact,
            pr_review=pr_review,
            test_model=test_model,
            debug=debug,
        )
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


def _run_main(options: CliOptions) -> int:
    """Run main application logic using CliOptions.

    Avoids Click's parameter explosion by accepting a grouped options
    object instead of individual parameters.

    Args:
        options: CLI options grouped into a dataclass

    Returns:
        Exit code from PiRunner

    """
    # Get settings
    settings = get_settings(options)

    # Initialize paths
    paths = Paths()

    # Create runner
    runner = PiRunner(settings, paths)

    # Run the loop
    return runner.run()


if __name__ == "__main__":
    main()
