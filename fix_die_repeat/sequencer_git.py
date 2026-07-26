"""Read-only Git probes for sequencer workflows."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import threading
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from fix_die_repeat.utils import (
    COMMAND_TIMEOUT_EXIT_CODE,
    RunCommandOptions,
    run_command,
)

GIT_TIMEOUT_SECONDS = 30.0
MAX_UNTRACKED_BYTES = 64 * 1024 * 1024
MAX_TRACKED_DIFF_BYTES = 64 * 1024 * 1024


class GitProbeError(RuntimeError):
    """A Git or filesystem probe could not produce reliable truth."""


class _Digest(Protocol):
    """Hash object surface used by the streaming snapshot."""

    def update(self, value: bytes) -> None:
        """Add bytes to the digest."""


@dataclass(frozen=True)
class _GitResult:
    """Internal Git command result."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RepositoryInfo:
    """Canonical identity for one Git worktree."""

    root: Path
    common_dir: Path
    key: str


@dataclass(frozen=True)
class DirtyState:
    """Live staged, unstaged, and untracked state."""

    staged: bool
    unstaged: bool
    untracked: bool


@dataclass(frozen=True)
class GitSnapshot:
    """Content and HEAD snapshot used by Git predicates."""

    head: str | None
    symbolic_ref: str | None
    dirty: DirtyState
    digest: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible state representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> GitSnapshot:
        """Restore a snapshot from persisted state."""
        required = {"head", "symbolic_ref", "dirty", "digest"}
        dirty_required = {"staged", "unstaged", "untracked"}
        if not isinstance(value, dict) or set(value) != required:
            msg = "Invalid persisted Git snapshot"
            raise GitProbeError(msg)
        dirty_value = value["dirty"]
        if (
            not isinstance(dirty_value, dict)
            or set(dirty_value) != dirty_required
            or not all(isinstance(dirty_value[key], bool) for key in dirty_required)
            or not (value["head"] is None or isinstance(value["head"], str))
            or not (value["symbolic_ref"] is None or isinstance(value["symbolic_ref"], str))
            or not isinstance(value["digest"], str)
        ):
            msg = "Invalid persisted Git snapshot"
            raise GitProbeError(msg)
        dirty = DirtyState(**dirty_value)
        return cls(
            head=value["head"],
            symbolic_ref=value["symbolic_ref"],
            dirty=dirty,
            digest=value["digest"],
        )


def _run_git(
    repo: Path,
    args: list[str],
    *,
    check: bool = True,
) -> _GitResult:
    git_path = shutil.which("git")
    if git_path is None:
        msg = "Git executable is not available"
        raise GitProbeError(msg)
    returncode, stdout, stderr = run_command(
        [git_path, "-C", str(repo), *args],
        check=False,
        options=RunCommandOptions(
            encoding_errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
        ),
    )
    result = _GitResult(returncode=returncode, stdout=stdout, stderr=stderr)
    if check and result.returncode != 0:
        diagnostic = result.stderr.strip()
        msg = f"Git probe failed ({' '.join(args)}): {diagnostic}"
        raise GitProbeError(msg)
    return result


def _stdout(repo: Path, *args: str) -> str:
    result = _run_git(repo, list(args))
    return result.stdout.strip()


def _read_bounded_output(
    process: subprocess.Popen[bytes],
    limit: int,
    output: bytearray,
    exceeded: threading.Event,
    errors: list[Exception],
) -> None:
    if process.stdout is None:
        return
    try:
        while chunk := process.stdout.read(1024 * 1024):
            remaining = limit + 1 - len(output)
            if remaining > 0:
                output.extend(chunk[:remaining])
            if len(chunk) > remaining or len(output) > limit:
                exceeded.set()
                with suppress(OSError):
                    process.kill()
                return
    except (OSError, ValueError) as exc:
        errors.append(exc)
        with suppress(OSError):
            process.kill()


def _run_git_bounded(repo: Path, args: list[str], limit: int, subject: str) -> bytes:
    """Run Git while retaining no more than one byte beyond an output limit."""
    git_path = shutil.which("git")
    if git_path is None:
        msg = "Git executable is not available"
        raise GitProbeError(msg)
    with subprocess.Popen(  # noqa: S603  # Git path and argv are controlled here.
        [git_path, "-C", str(repo), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    ) as process:
        output = bytearray()
        exceeded = threading.Event()
        reader_errors: list[Exception] = []
        reader = threading.Thread(
            target=_read_bounded_output,
            args=(process, limit, output, exceeded, reader_errors),
            daemon=True,
        )
        try:
            reader.start()
            try:
                returncode = process.wait(timeout=GIT_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                reader.join(GIT_TIMEOUT_SECONDS)
                msg = f"Git probe timed out ({' '.join(args)})"
                raise GitProbeError(msg) from exc
            reader.join(GIT_TIMEOUT_SECONDS)
            if reader.is_alive():
                msg = f"Git probe output reader did not finish ({' '.join(args)})"
                raise GitProbeError(msg)
        finally:
            if process.poll() is None:
                with suppress(OSError):
                    process.kill()
                with suppress(OSError):
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()

    if reader_errors:
        msg = f"Cannot read Git probe output: {reader_errors[0]}"
        raise GitProbeError(msg) from reader_errors[0]
    if exceeded.is_set():
        msg = (
            f"{subject} content exceeds the remaining {limit} byte snapshot budget "
            f"within the {MAX_TRACKED_DIFF_BYTES} byte total"
        )
        raise GitProbeError(msg)
    if returncode != 0:
        diagnostic = bytes(output).decode(errors="surrogateescape").strip()
        msg = f"Git probe failed ({' '.join(args)}): {diagnostic}"
        raise GitProbeError(msg)
    return bytes(output)


def resolve_repository(path: Path) -> RepositoryInfo:
    """Resolve one worktree without modifying Git state."""
    candidate = path.expanduser().resolve(strict=False)
    result = _run_git(candidate, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode == COMMAND_TIMEOUT_EXIT_CODE:
        diagnostic = result.stderr.strip()
        msg = f"Git repository probe timed out: {diagnostic}"
        raise GitProbeError(msg)
    if result.returncode != 0:
        msg = f"{candidate} is not a Git working tree"
        raise GitProbeError(msg)
    root_text = result.stdout.strip()
    if not root_text:
        msg = "Git repository probe returned an empty repository root"
        raise GitProbeError(msg)
    root = Path(root_text).resolve()
    common_raw = _stdout(root, "rev-parse", "--git-common-dir")
    common_path = Path(common_raw)
    if not common_path.is_absolute():
        common_path = root / common_path
    common_dir = common_path.resolve()
    digest = hashlib.sha256(f"{root}\0{common_dir}".encode()).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", root.name) or "repo"
    key = f"{safe_name}-{digest[:16]}"
    return RepositoryInfo(root=root, common_dir=common_dir, key=key)


def _head(repo: Path) -> tuple[str | None, str | None]:
    head_result = _run_git(repo, ["rev-parse", "--verify", "HEAD"], check=False)
    if head_result.returncode == COMMAND_TIMEOUT_EXIT_CODE:
        msg = f"Git HEAD probe failed: {head_result.stderr.strip()}"
        raise GitProbeError(msg)

    ref_result = _run_git(repo, ["symbolic-ref", "-q", "HEAD"], check=False)
    if ref_result.returncode not in {0, 1}:
        diagnostic = ref_result.stderr.strip()
        msg = f"Git symbolic-ref probe failed: {diagnostic}"
        raise GitProbeError(msg)
    symbolic_ref = ref_result.stdout.strip() if ref_result.returncode == 0 else None
    if head_result.returncode == 0:
        return head_result.stdout.strip(), symbolic_ref
    if symbolic_ref is not None:
        ref_check = _run_git(
            repo,
            ["show-ref", "--verify", "--quiet", symbolic_ref],
            check=False,
        )
        if ref_check.returncode == 1:
            return None, symbolic_ref
    diagnostic = head_result.stderr.strip()
    msg = f"Git HEAD probe failed: {diagnostic}"
    raise GitProbeError(msg)


def _dirty(repo: Path) -> DirtyState:
    staged = bool(_run_git(repo, ["diff", "--cached", "--name-only"]).stdout)
    unstaged = bool(_run_git(repo, ["diff", "--name-only"]).stdout)
    untracked = bool(
        _run_git(repo, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout,
    )
    return DirtyState(staged=staged, unstaged=unstaged, untracked=untracked)


def _untracked_entries(
    repo: Path,
    relative_paths: list[str],
) -> list[tuple[str, Path, os.stat_result]]:
    entries: list[tuple[str, Path, os.stat_result]] = []
    regular_file_bytes = 0
    for relative_path in relative_paths:
        path = repo / relative_path
        try:
            metadata = path.lstat()
        except OSError as exc:
            msg = f"Cannot inspect untracked path {path}: {exc}"
            raise GitProbeError(msg) from exc
        if stat.S_ISREG(metadata.st_mode):
            regular_file_bytes += metadata.st_size
            if regular_file_bytes > MAX_UNTRACKED_BYTES:
                msg = "Untracked regular-file content exceeds the 64 MiB snapshot limit"
                raise GitProbeError(msg)
        entries.append((relative_path, path, metadata))
    return entries


def _update_untracked_digest(
    repo: Path,
    digest: _Digest,
    relative_paths: list[str],
) -> None:
    read_bytes = 0
    for relative_path, path, metadata in _untracked_entries(repo, relative_paths):
        _update_digest_field(digest, b"untracked")
        _update_digest_field(digest, os.fsencode(relative_path))
        _update_digest_field(digest, str(metadata.st_mode).encode())
        if stat.S_ISREG(metadata.st_mode):
            _update_digest_field(digest, b"regular")
            try:
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        read_bytes += len(chunk)
                        if read_bytes > MAX_UNTRACKED_BYTES:
                            msg = "Untracked regular-file content exceeds the 64 MiB snapshot limit"
                            raise GitProbeError(msg)
                        _update_digest_field(digest, chunk)
                    _update_digest_field(digest, b"")
            except OSError as exc:
                msg = f"Cannot read untracked file {path}: {exc}"
                raise GitProbeError(msg) from exc
        elif stat.S_ISLNK(metadata.st_mode):
            _update_digest_field(digest, b"symlink")
            try:
                _update_digest_field(digest, os.fsencode(path.readlink()))
            except OSError as exc:
                msg = f"Cannot read untracked symlink {path}: {exc}"
                raise GitProbeError(msg) from exc
        else:
            _update_digest_field(digest, b"other")


def _update_digest_field(digest: _Digest, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def capture_snapshot(repository: RepositoryInfo) -> GitSnapshot:
    """Capture content and HEAD state without writing Git objects."""
    head, symbolic_ref = _head(repository.root)
    staged = _run_git_bounded(
        repository.root,
        ["diff", "--cached", "--binary", "--no-ext-diff"],
        MAX_TRACKED_DIFF_BYTES,
        "Staged diff",
    )
    unstaged = _run_git_bounded(
        repository.root,
        ["diff", "--binary", "--no-ext-diff"],
        MAX_TRACKED_DIFF_BYTES - len(staged),
        "Unstaged diff",
    )
    untracked_output = _run_git(
        repository.root,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ).stdout
    untracked_paths = sorted(filter(None, untracked_output.split("\0")))
    dirty = DirtyState(
        staged=bool(staged),
        unstaged=bool(unstaged),
        untracked=bool(untracked_paths),
    )
    digest = hashlib.sha256()
    _update_digest_field(digest, (head or "<unborn>").encode())
    _update_digest_field(digest, (symbolic_ref or "<detached>").encode())
    _update_digest_field(digest, b"staged")
    _update_digest_field(digest, staged)
    _update_digest_field(digest, b"unstaged")
    _update_digest_field(digest, unstaged)
    _update_untracked_digest(repository.root, digest, untracked_paths)
    return GitSnapshot(
        head=head,
        symbolic_ref=symbolic_ref,
        dirty=dirty,
        digest=digest.hexdigest(),
    )


def _configured_upstream(repo: Path, symbolic_ref: str | None) -> tuple[str | None, bool]:
    if symbolic_ref is None:
        return None, False
    branch = symbolic_ref.removeprefix("refs/heads/")
    remote = _run_git(repo, ["config", "--get", f"branch.{branch}.remote"], check=False)
    merge = _run_git(repo, ["config", "--get", f"branch.{branch}.merge"], check=False)
    configured = remote.returncode == 0 or merge.returncode == 0
    upstream = _run_git(
        repo,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        check=False,
    )
    if upstream.returncode == 0:
        return upstream.stdout.strip(), True
    return None, configured


def _has_unpushed(repository: RepositoryInfo) -> bool:
    head, symbolic_ref = _head(repository.root)
    if head is None:
        return False
    upstream, configured = _configured_upstream(repository.root, symbolic_ref)
    if upstream is not None:
        count = _stdout(repository.root, "rev-list", "--count", f"{upstream}..HEAD")
        try:
            return int(count) > 0
        except ValueError as exc:
            msg = f"Git rev-list returned an unparsable count: {count!r}"
            raise GitProbeError(msg) from exc
    if configured:
        msg = "The configured upstream no longer resolves"
        raise GitProbeError(msg)

    refs = _stdout(repository.root, "for-each-ref", "--format=%(refname)", "refs/remotes")
    if not refs:
        msg = "No remote-tracking refs can establish whether HEAD was pushed"
        raise GitProbeError(msg)
    containing = _stdout(
        repository.root,
        "for-each-ref",
        "--contains",
        "HEAD",
        "--format=%(refname)",
        "refs/remotes",
    )
    return not bool(containing)


def evaluate_git_operation(
    operation: str,
    repository: RepositoryInfo,
    *,
    initial: GitSnapshot | None = None,
    issued: GitSnapshot | None = None,
) -> bool:
    """Evaluate one closed-registry Git operation."""
    if operation == "git.has_unpushed_commits":
        return _has_unpushed(repository)

    result: bool
    if operation in {
        "git.is_clean",
        "git.has_staged_changes",
        "git.has_unstaged_changes",
        "git.has_untracked_changes",
    }:
        dirty = _dirty(repository.root)
        if operation == "git.is_clean":
            result = not any(asdict(dirty).values())
        elif operation == "git.has_staged_changes":
            result = dirty.staged
        elif operation == "git.has_unstaged_changes":
            result = dirty.unstaged
        else:
            result = dirty.untracked
    elif operation == "git.head_changed":
        if initial is None:
            msg = "git.head_changed requires the initial snapshot"
            raise GitProbeError(msg)
        current_head, current_symbolic_ref = _head(repository.root)
        result = (current_head, current_symbolic_ref) != (
            initial.head,
            initial.symbolic_ref,
        )
    elif operation == "git.working_tree_changed":
        if issued is None:
            msg = "git.working_tree_changed requires an issued-step snapshot"
            raise GitProbeError(msg)
        current = capture_snapshot(repository)
        result = current.digest != issued.digest
    else:
        msg = f"Unsupported Git operation: {operation}"
        raise GitProbeError(msg)
    return result
