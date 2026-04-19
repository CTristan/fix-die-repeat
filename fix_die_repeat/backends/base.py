"""Backend protocol and request/result dataclasses."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class BackendRequest:
    """A single agent invocation described in backend-agnostic terms.

    Each backend is responsible for translating this into its own CLI
    surface (e.g. pi's `-p`/`--tools`/`@file`/prompt argv layout).
    """

    prompt: str
    tools: tuple[str, ...] = ()
    attachments: tuple[Path, ...] = ()
    model: str | None = None


@dataclass(frozen=True)
class BackendResult:
    """Result of a backend invocation."""

    returncode: int
    stdout: str
    stderr: str


@runtime_checkable
class Backend(Protocol):
    """Structural interface every backend implementation must provide."""

    def invoke(self, request: BackendRequest) -> BackendResult:
        """Run the agent once with no retry."""
        ...

    def invoke_safe(self, request: BackendRequest) -> BackendResult:
        """Run the agent with backend-appropriate retry on transient failure."""
        ...


__all__ = ["Backend", "BackendRequest", "BackendResult"]
