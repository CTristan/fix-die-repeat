"""End-to-end integration test for the pi-bridge.

Spawns a real ``node priv/pi-bridge/bridge.js`` subprocess and verifies the
init → ready → shutdown handshake works. Skipped when ``node`` is not on
PATH so unit test runs remain offline and fast.

No model credentials required: the test only exercises the handshake, not
``prompt``.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from fix_die_repeat.bridge_install import BridgeInstallError, _check_node_version
from fix_die_repeat.pi_bridge import PiBridge, PiBridgeConfig


def _node_skip_reason() -> str | None:
    node = shutil.which("node")
    if node is None:
        return "node not on PATH"
    try:
        _check_node_version(node, logging.getLogger("test-bridge-integration.node-check"))
    except BridgeInstallError as err:
        return str(err)
    return None


_NODE_SKIP_REASON = _node_skip_reason()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _NODE_SKIP_REASON is not None,
        reason=_NODE_SKIP_REASON or "node environment unavailable",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_DIR = REPO_ROOT / "priv" / "pi-bridge"


def _bridge_script() -> Path:
    script = BRIDGE_DIR / "bridge.js"
    if not script.exists():
        pytest.skip(f"bridge script missing: {script}")
    if not (BRIDGE_DIR / "node_modules").exists():
        pytest.skip(
            f"bridge deps not installed at {BRIDGE_DIR / 'node_modules'}; "
            "run `cd priv/pi-bridge && npm ci` before the integration suite",
        )
    return script


def test_init_ready_shutdown_round_trip(tmp_path: Path) -> None:
    """Bridge responds to init with ready and exits cleanly on shutdown."""
    config = PiBridgeConfig(
        working_dir=tmp_path,
        tools=("read",),  # minimal, won't be invoked
    )
    logger = logging.getLogger("test-bridge-integration")
    logger.setLevel(logging.DEBUG)

    with PiBridge(config, bridge_script=_bridge_script(), logger=logger):
        # If we got here, init → ready worked.
        pass
    # __exit__ sends shutdown; process should have exited with code 0.
