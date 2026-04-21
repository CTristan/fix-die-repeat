"""Idempotent installer for the pi-bridge Node.js dependencies.

Stages the shipped bridge files (``bridge.js``, ``package.json``,
``package-lock.json``) from a **source** directory into a writable **runtime**
directory, runs ``npm ci`` (or ``npm install`` if no lockfile) there on first
use, writes a marker file on success, and short-circuits on subsequent runs
when the marker exists and matches the expected version.

The separation matters because the default source dir lives inside the
installed wheel/site-packages, which is often read-only — writing
``node_modules/`` there would break first-run on system Python installs even
when ``FDR_HOME`` is writable. The runtime dir is always under ``FDR_HOME``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from pathlib import Path

INSTALL_MARKER = ".install-marker"
_INSTALL_TIMEOUT_SECONDS = 600  # npm ci can be slow on cold caches
_STAGED_FILES = ("bridge.js", "package.json", "package-lock.json")


class BridgeInstallError(RuntimeError):
    """Raised when the pi-bridge dependencies cannot be installed."""


def _read_package_version(package_json: Path, dep_name: str) -> str:
    """Return the pinned version of ``dep_name`` in a bridge ``package.json``."""
    try:
        data = json.loads(package_json.read_text())
    except (OSError, json.JSONDecodeError) as err:
        msg = f"Could not read {package_json}: {err}"
        raise BridgeInstallError(msg) from err
    version = data.get("dependencies", {}).get(dep_name)
    if not isinstance(version, str) or not version:
        msg = f"{package_json} is missing the '{dep_name}' dependency entry"
        raise BridgeInstallError(msg)
    return version


def _marker_matches(marker: Path, expected_token: str) -> bool:
    """Return True when the install marker records ``expected_token``."""
    if not marker.exists():
        return False
    try:
        content = marker.read_text().strip()
    except OSError:
        return False
    return content == expected_token


def _compute_install_hash(source_dir: Path) -> str:
    """Return a SHA-256 digest over the shipped bridge files in ``source_dir``.

    Hashing every staged file (``bridge.js``, ``package.json``,
    ``package-lock.json``) — including whether each one is present — means
    any shipped change forces a reinstall/restage. A version bump of
    ``@mariozechner/pi-ai``, a refresh of the lockfile, or an edit to
    ``bridge.js`` all change this digest even when the
    ``@mariozechner/pi-coding-agent`` pin is unchanged.
    """
    digest = hashlib.sha256()
    for name in _STAGED_FILES:
        src = source_dir / name
        digest.update(name.encode("utf-8"))
        if src.exists():
            digest.update(b"\x00present\x00")
            digest.update(src.read_bytes())
        else:
            digest.update(b"\x00absent\x00")
    return digest.hexdigest()


def _missing_dependency_error(tool: str) -> BridgeInstallError:
    """Build a ``BridgeInstallError`` for a missing CLI tool (node or npm)."""
    msg = (
        f"fix-die-repeat requires {tool} on PATH for the pi bridge. "
        "Install Node.js >=20 (via Homebrew, nvm, or https://nodejs.org) and re-run."
    )
    return BridgeInstallError(msg)


def _stage_files(source_dir: Path, runtime_dir: Path) -> None:
    """Sync shipped bridge files from ``source_dir`` into ``runtime_dir``.

    Files present in source are copied unconditionally so a source-side bump
    reliably propagates on the next install. Files absent in source are also
    removed from runtime so stale staged artifacts (for example an old
    ``package-lock.json``) cannot affect later install behavior.
    """
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for name in _STAGED_FILES:
        src = source_dir / name
        dest = runtime_dir / name
        if not src.exists():
            if dest.exists() or dest.is_symlink():
                dest.unlink()
            continue
        shutil.copy2(src, dest)


def ensure_bridge_installed(
    source_dir: Path,
    runtime_dir: Path,
    *,
    logger: logging.Logger,
    pi_package: str = "@mariozechner/pi-coding-agent",
) -> Path:
    """Stage + install the pi-bridge; return the runtime ``bridge.js`` path.

    ``source_dir`` is where the shipped files live (inside the installed
    wheel by default, or an ``FDR_BRIDGE_DIR`` override for dev checkouts).
    ``runtime_dir`` is where ``node_modules/`` gets installed and where Node
    will launch from — it must be writable. ``source_dir == runtime_dir`` is
    supported and skips the copy step.

    Raises :class:`BridgeInstallError` if ``node``/``npm`` are missing, if the
    install fails, or if the source directory is malformed.
    """
    source_bridge_script = source_dir / "bridge.js"
    source_package_json = source_dir / "package.json"

    if not source_bridge_script.exists():
        msg = f"pi-bridge script missing: {source_bridge_script}"
        raise BridgeInstallError(msg)
    if not source_package_json.exists():
        msg = f"pi-bridge manifest missing: {source_package_json}"
        raise BridgeInstallError(msg)

    expected_version = _read_package_version(source_package_json, pi_package)
    expected_hash = _compute_install_hash(source_dir)

    runtime_marker = runtime_dir / "node_modules" / INSTALL_MARKER
    runtime_bridge_script = runtime_dir / "bridge.js"

    # Short-circuit: runtime already has matching deps installed.
    if runtime_bridge_script.exists() and _marker_matches(runtime_marker, expected_hash):
        logger.debug(
            "pi-bridge already installed in %s (%s=%s); skipping npm ci",
            runtime_dir,
            pi_package,
            expected_version,
        )
        return runtime_bridge_script

    if shutil.which("node") is None:
        err_node = _missing_dependency_error("Node.js")
        raise err_node
    if shutil.which("npm") is None:
        err_npm = _missing_dependency_error("npm")
        raise err_npm

    # Stage shipped files into runtime_dir (no-op when source == runtime).
    if source_dir.resolve() != runtime_dir.resolve():
        _stage_files(source_dir, runtime_dir)

    runtime_lockfile = runtime_dir / "package-lock.json"
    install_cmd = ["npm", "ci"] if runtime_lockfile.exists() else ["npm", "install"]
    logger.info(
        "Installing pi-bridge dependencies (%s) in %s...", " ".join(install_cmd), runtime_dir
    )

    try:
        result = subprocess.run(  # noqa: S603 — trusted npm binary
            install_cmd,
            cwd=runtime_dir,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        msg = f"pi-bridge dependency install timed out after {_INSTALL_TIMEOUT_SECONDS}s"
        raise BridgeInstallError(msg) from err
    except FileNotFoundError as err:
        err_npm_exec = _missing_dependency_error("npm")
        raise err_npm_exec from err

    if result.returncode != 0:
        msg = (
            f"pi-bridge dependency install failed (exit {result.returncode})."
            f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        raise BridgeInstallError(msg)

    runtime_node_modules = runtime_dir / "node_modules"
    runtime_node_modules.mkdir(parents=True, exist_ok=True)
    runtime_marker.write_text(expected_hash)
    logger.info("pi-bridge dependencies installed (%s=%s)", pi_package, expected_version)
    return runtime_bridge_script
