"""PR review management for fix-die-repeat runner.

This module handles GitHub PR review operations including:
- Fetching PR threads via GraphQL
- Formatting and caching PR threads
- Resolving threads on GitHub
- Limiting threads based on settings
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from fix_die_repeat.config import Paths, Settings
from fix_die_repeat.messages import (
    pr_threads_safe_only_message,
    pr_threads_unsafe_count_warning,
)
from fix_die_repeat.prompts import render_prompt
from fix_die_repeat.utils import run_command


@dataclass(frozen=True)
class PrInfo:
    """PR information from GitHub."""

    number: int
    url: str
    repo_owner: str
    repo_name: str


class PrReviewManager:
    """Manages PR review operations for the fix-die-repeat runner.

    Handles:
    - Fetching PR information and threads
    - Formatting threads for prompts
    - Resolving threads via GitHub GraphQL
    - Caching threads to avoid refetching
    """

    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        project_root: Path,
        logger: logging.Logger,
        iteration: int = 0,
    ) -> None:
        """Initialize the PR review manager.

        Args:
            settings: Configuration settings
            paths: Path management
            project_root: Project root directory
            logger: Logger instance for output
            iteration: Current iteration number (for introspection markers)

        """
        self.settings = settings
        self.paths = paths
        self.project_root = project_root
        self.logger = logger
        self.iteration = iteration

    def get_branch_name(self) -> str | None:
        """Get the current git branch name.

        Returns:
            Branch name or None if not on a branch

        """
        returncode, branch, _ = run_command(
            "git branch --show-current",
            cwd=self.project_root,
        )
        if returncode != 0 or not branch.strip():
            return None
        return branch.strip()

    def get_pr_info(self, branch: str) -> PrInfo | None:
        """Get PR information for a branch.

        Args:
            branch: Branch name

        Returns:
            PrInfo object or None if not found

        """
        pr_json = self._fetch_pr_info_json(branch)
        if pr_json is None:
            return None

        pr_data = self._parse_pr_info_payload(branch, pr_json)
        if pr_data is None:
            return None

        return self._build_pr_info(branch, pr_json, pr_data)

    def _fetch_pr_info_json(self, branch: str) -> str | None:
        """Fetch PR info JSON for a branch."""
        returncode, pr_json, _ = run_command(
            f"gh pr view {branch} --json number,url,headRepository,headRepositoryOwner",
            cwd=self.project_root,
        )
        if returncode != 0:
            return None
        return pr_json

    def _parse_pr_info_payload(
        self,
        branch: str,
        pr_json: str,
    ) -> dict[str, object] | None:
        """Parse PR info JSON into a payload dictionary."""
        try:
            pr_data = json.loads(pr_json)
        except json.JSONDecodeError:
            self.logger.exception(
                "Failed to parse PR info from gh output for branch %s: %s",
                branch,
                pr_json,
            )
            return None

        if not isinstance(pr_data, dict):
            self.logger.error(
                "Failed to parse PR info from gh output for branch %s: %s",
                branch,
                pr_json,
            )
            return None

        return pr_data

    def _build_pr_info(
        self,
        branch: str,
        pr_json: str,
        pr_data: dict[str, object],
    ) -> PrInfo | None:
        """Build a PrInfo object from a parsed payload."""
        number = pr_data.get("number")
        url = pr_data.get("url")
        repo_owner_payload = pr_data.get("headRepositoryOwner")
        repo_payload = pr_data.get("headRepository")

        if not isinstance(number, int) or not isinstance(url, str) or not url:
            self.logger.error(
                "Invalid PR info types from gh output for branch %s: "
                "number=%r (type=%s), url=%r (type=%s)",
                branch,
                number,
                type(number).__name__,
                url,
                type(url).__name__,
            )
            return None

        if not isinstance(repo_owner_payload, dict) or not isinstance(repo_payload, dict):
            self.logger.error(
                "Failed to parse PR info from gh output for branch %s: %s",
                branch,
                pr_json,
            )
            return None

        repo_owner = repo_owner_payload.get("login")
        repo_name = repo_payload.get("name")

        if not isinstance(repo_owner, str) or not isinstance(repo_name, str):
            self.logger.error(
                "Invalid repository info types from gh output for branch %s: "
                "owner=%r (type=%s), name=%r (type=%s)",
                branch,
                repo_owner,
                type(repo_owner).__name__,
                repo_name,
                type(repo_name).__name__,
            )
            return None

        return PrInfo(
            number=number,
            url=url,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    def check_pr_threads_cache(self, cache_key: str) -> bool:
        """Check if cached PR threads are valid and use them.

        Args:
            cache_key: Cache key to validate

        Returns:
            True if cache was used, False otherwise

        """
        has_valid_cache = (
            self.paths.pr_threads_cache.exists()
            and self.paths.pr_threads_hash_file.exists()
            and self.paths.pr_threads_hash_file.read_text() == cache_key
        )
        if not has_valid_cache:
            return False

        if not self.paths.pr_thread_ids_file.exists():
            self.logger.warning(
                "PR thread cache exists but in-scope ID file is missing. Refetching threads.",
            )
            return False

        in_scope_ids = [
            thread_id.strip()
            for thread_id in self.paths.pr_thread_ids_file.read_text().splitlines()
            if thread_id.strip()
        ]
        if not in_scope_ids:
            self.logger.warning(
                "PR thread cache exists but in-scope ID file is empty. Refetching threads.",
            )
            return False

        self.logger.info("Using cached PR threads (unchanged)...")
        cached_content = self.paths.pr_threads_cache.read_text()
        self.paths.review_current_file.write_text(cached_content)
        self._persist_in_scope_thread_ids(in_scope_ids)
        self.logger.info("Found %s unresolved threads from cache.", len(in_scope_ids))
        return True

    @staticmethod
    def _extract_thread_ids(threads: list[dict]) -> list[str]:
        """Extract GraphQL thread IDs from thread dictionaries.

        Args:
            threads: Thread dictionaries

        Returns:
            Thread IDs in list order

        """
        return [str(thread["id"]) for thread in threads if thread.get("id")]

    def _persist_in_scope_thread_ids(self, thread_ids: list[str]) -> None:
        """Persist in-scope PR thread IDs for safe resolution.

        Args:
            thread_ids: Thread IDs to persist

        """
        unique_ids = list(dict.fromkeys(thread_id for thread_id in thread_ids if thread_id))

        if unique_ids:
            self.paths.pr_thread_ids_file.write_text("\n".join(unique_ids) + "\n")

            # Also append to cumulative file for introspection
            existing_cumulative: set[str] = set()
            if self.paths.cumulative_in_scope_threads_file.exists():
                existing_cumulative = {
                    line.strip()
                    for line in self.paths.cumulative_in_scope_threads_file.read_text().splitlines()
                    if line.strip()
                }
            new_ids = set(unique_ids) - existing_cumulative
            if new_ids:
                with self.paths.cumulative_in_scope_threads_file.open("a") as f:
                    for thread_id in sorted(new_ids):
                        f.write(f"{thread_id}\n")
            return

        self.paths.pr_thread_ids_file.unlink(missing_ok=True)

    @staticmethod
    def _latest_thread_comment_timestamp(thread: dict) -> str:
        """Get the latest comment timestamp available for a review thread.

        Args:
            thread: Review thread payload from GraphQL

        Returns:
            Latest ``createdAt`` timestamp as an ISO string, or empty string

        """
        comments = thread.get("comments")
        if not isinstance(comments, dict):
            return ""

        nodes = comments.get("nodes")
        if not isinstance(nodes, list):
            return ""

        timestamps = [
            str(node.get("createdAt"))
            for node in nodes
            if isinstance(node, dict) and node.get("createdAt")
        ]
        if not timestamps:
            return ""

        return max(timestamps)

    def _limit_unresolved_threads(self, unresolved_threads: list[dict]) -> list[dict]:
        """Limit unresolved PR threads based on settings.max_pr_threads.

        Args:
            unresolved_threads: All unresolved PR threads

        Returns:
            Limited unresolved threads suitable for the prompt

        """
        total_unresolved = len(unresolved_threads)
        max_threads = self.settings.max_pr_threads

        if not isinstance(max_threads, int) or max_threads <= 0 or total_unresolved <= max_threads:
            return unresolved_threads

        unresolved_threads = sorted(
            unresolved_threads,
            key=lambda thread: (
                self._latest_thread_comment_timestamp(thread),
                str(thread.get("id") or ""),
            ),
            reverse=True,
        )
        limited_threads = unresolved_threads[:max_threads]
        skipped_threads = unresolved_threads[max_threads:]
        skipped_ids = self._extract_thread_ids(skipped_threads)

        self.logger.warning(
            pr_threads_unsafe_count_warning(len(skipped_threads), skipped_ids),
        )
        self.logger.info(pr_threads_safe_only_message(len(limited_threads)))

        return limited_threads

    def fetch_pr_threads_gql(
        self,
        repo_owner: str,
        repo_name: str,
        pr_number: int,
    ) -> list[dict] | None:
        """Fetch PR threads via GraphQL.

        Args:
            repo_owner: Repository owner
            repo_name: Repository name
            pr_number: PR number

        Returns:
            Thread data list or None on failure

        """
        query = """
            query($owner: String!, $repo: String!, $number: Int!) {
                repository(owner: $owner, name: $repo) {
                    pullRequest(number: $number) {
                        reviewThreads(first: 100) {
                            nodes {
                                isResolved
                                id
                                path
                                line
                                comments(last: 10) {
                                    nodes {
                                        author { login }
                                        body
                                        createdAt
                                    }
                                }
                            }
                        }
                    }
                }
            }
        """

        query_single_line = " ".join(line.strip() for line in query.splitlines() if line.strip())
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query_single_line}",
            "-F",
            f"owner={repo_owner}",
            "-F",
            f"repo={repo_name}",
            "-F",
            f"number={pr_number}",
        ]

        returncode, gql_result, _ = run_command(cmd, cwd=self.project_root)
        if returncode != 0:
            return None

        try:
            data = json.loads(gql_result)
            return data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        except (json.JSONDecodeError, KeyError):
            self.logger.exception("Failed to parse PR thread data")
            return None

    @staticmethod
    def _format_thread_comment(author: str, body: str) -> list[str]:
        """Format a thread comment without exposing parser-sensitive prefixes.

        Args:
            author: Comment author login
            body: Raw comment body

        Returns:
            Formatted comment lines safe to embed in cached markdown

        """
        comment_lines = body.splitlines() or [""]
        formatted_lines = [f"[{author}]: {comment_lines[0]}"]
        formatted_lines.extend(f"    {line}" for line in comment_lines[1:])
        return formatted_lines

    def format_pr_threads(self, threads: list, pr_number: int, pr_url: str) -> str:
        """Format PR threads for display.

        Args:
            threads: List of thread data
            pr_number: PR number
            pr_url: PR URL

        Returns:
            Formatted thread content

        """
        threads_output = []
        for i, thread in enumerate(threads, 1):
            threads_output.append(f"--- Thread #{i} ---")
            threads_output.append(f"ID: {thread['id']}")
            threads_output.append(f"File: {thread.get('path', 'N/A')}")
            if thread.get("line"):
                threads_output.append(f"Line: {thread['line']}")

            for comment in thread["comments"]["nodes"]:
                author = comment["author"]["login"] if comment.get("author") else "unknown"
                body = str(comment.get("body") or "")
                threads_output.extend(self._format_thread_comment(author, body))

            threads_output.append("")

        header = render_prompt(
            "pr_threads_header.j2",
            unresolved_count=len(threads),
            pr_number=pr_number,
            pr_url=pr_url,
        )

        return f"{header}\n\n" + "\n".join(threads_output)

    def fetch_pr_threads(self) -> None:
        """Fetch PR threads for PR review mode."""
        self.logger.info("[Step 3.5] Checking for unresolved PR threads...")

        branch = self.get_branch_name()
        if not branch:
            self.logger.error("Not on a git branch. Skipping PR review.")
            return

        self.logger.info("Fetching PR info for branch: %s", branch)

        # Check gh auth
        returncode, _, _ = run_command("gh auth status", cwd=self.project_root)
        if returncode != 0:
            self.logger.error("GitHub CLI not authenticated. Skipping PR review.")
            return

        pr_info = self.get_pr_info(branch)
        if not pr_info:
            self.logger.info(
                "No open PR found for %s or error fetching PR. Skipping PR review.",
                branch,
            )
            return

        pr_number = pr_info.number
        pr_url = pr_info.url
        repo_owner = pr_info.repo_owner
        repo_name = pr_info.repo_name

        self.logger.info(
            "Found PR #%s (%s). Checking for cached threads...",
            pr_number,
            pr_url,
        )

        cache_key = f"{repo_owner}/{repo_name}/{pr_number}"
        if self.check_pr_threads_cache(cache_key):
            return

        self.logger.info("Cache miss or invalid. Fetching fresh threads...")

        threads = self.fetch_pr_threads_gql(repo_owner, repo_name, pr_number)
        if threads is None:
            return

        unresolved_threads = [t for t in threads if not t.get("isResolved", True)]
        unresolved_threads = self._limit_unresolved_threads(unresolved_threads)

        if unresolved_threads:
            content = self.format_pr_threads(unresolved_threads, pr_number, pr_url)
            self.paths.review_current_file.write_text(content)
            thread_ids = self._extract_thread_ids(unresolved_threads)
            self._persist_in_scope_thread_ids(thread_ids)
            self.logger.info(
                "Found %s unresolved threads. Added to review queue.",
                len(unresolved_threads),
            )

            # Cache
            self.paths.pr_threads_cache.write_text(content)
            self.paths.pr_threads_hash_file.write_text(cache_key)

            # Append to cumulative content file for introspection (with iteration marker)
            thread_count = len(unresolved_threads)
            with self.paths.cumulative_pr_threads_content_file.open("a") as f:
                f.write(f"# Iteration {self.iteration} - Fetched {thread_count} threads\n")
                f.write(content)
                f.write("\n")
        else:
            self.logger.info("No unresolved threads found.")
            self.paths.review_current_file.write_text("")
            self._persist_in_scope_thread_ids([])

    def resolve_pr_threads(self) -> None:
        """Resolve PR threads that were fixed."""
        if not self.paths.pr_resolved_threads_file.exists():
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        resolved_ids = self.paths.pr_resolved_threads_file.read_text().strip().split("\n")
        resolved_ids = [thread_id for thread_id in resolved_ids if thread_id]

        if not resolved_ids:
            self.logger.info("No threads were reported as resolved. Continuing to next iteration.")
            return

        self.logger.info("Model reported %s resolved thread(s).", len(resolved_ids))

        # Verify all resolved IDs were in scope
        in_scope_ids = []
        if self.paths.pr_thread_ids_file.exists():
            in_scope_ids = self.paths.pr_thread_ids_file.read_text().strip().split("\n")

        safe_resolved_ids = set(resolved_ids) & set(in_scope_ids)

        if len(safe_resolved_ids) < len(resolved_ids):
            unsafe_ids = set(resolved_ids) - set(in_scope_ids)
            self.logger.warning(
                pr_threads_unsafe_count_warning(len(unsafe_ids), list(unsafe_ids)),
            )
            self.logger.info(pr_threads_safe_only_message(len(safe_resolved_ids)))

        if safe_resolved_ids:
            # Build GraphQL mutation to resolve a single review thread
            # Note: resolveReviewThread only accepts one threadId at a time
            mutation = """
                mutation($threadId: ID!) {
                    resolveReviewThread(input: {threadId: $threadId}) {
                        thread {
                            id
                        }
                    }
                }
            """
            mutation_single_line = " ".join(
                line.strip() for line in mutation.splitlines() if line.strip()
            )

            # Resolve each thread individually (GitHub API limitation)
            resolved_count = 0
            for thread_id in safe_resolved_ids:
                self.logger.info(
                    "Resolving PR thread %s via gh GraphQL",
                    thread_id,
                )
                returncode, _gql_result, _ = run_command(
                    [
                        "gh",
                        "api",
                        "graphql",
                        "-f",
                        f"query={mutation_single_line}",
                        "-F",
                        f"threadId={thread_id}",
                    ],
                    cwd=self.project_root,
                )

                if returncode == 0:
                    resolved_count += 1
                    # Append to cumulative resolved threads file for introspection
                    with self.paths.cumulative_resolved_threads_file.open("a") as f:
                        f.write(f"{thread_id}\n")
                else:
                    self.logger.warning(
                        "Failed to resolve thread %s (exit code: %s)",
                        thread_id,
                        returncode,
                    )

            self.logger.info(
                "Successfully resolved %s of %s thread(s).",
                resolved_count,
                len(safe_resolved_ids),
            )
            # Invalidate cache and refetch
            self.paths.pr_threads_hash_file.unlink(missing_ok=True)
            self.fetch_pr_threads()

            if (
                not self.paths.review_current_file.exists()
                or not self.paths.review_current_file.read_text().strip()
            ):
                self.logger.info(
                    "All PR threads have been resolved! "
                    "Running final local diff review to catch any remaining issues.",
                )
                return

            remaining_count = self.paths.review_current_file.read_text().count(
                "--- Thread #",
            )
            self.logger.info(
                "%s PR threads remain. Continuing to next iteration.",
                remaining_count,
            )
        else:
            self.logger.info(
                "No in-scope threads were reported as resolved. Continuing to next iteration.",
            )

        # Clear resolved threads file
        self.paths.pr_resolved_threads_file.unlink(missing_ok=True)
