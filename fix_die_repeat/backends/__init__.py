"""Backend abstraction for AI coding agents.

Phase 1 of multi-backend support: `Backend` is a protocol covering prompt
delivery, tool flags, file attachments, and model selection. `PiBackend`
is the sole implementation today; future backends (Gemini, fallback chains)
plug in behind the same interface.
"""

from fix_die_repeat.backends.base import Backend, BackendRequest, BackendResult
from fix_die_repeat.backends.pi import PiBackend

__all__ = ["Backend", "BackendRequest", "BackendResult", "PiBackend"]
