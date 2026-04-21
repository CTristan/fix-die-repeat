# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Node.js sidecar bridge for pi invocation. `fix-die-repeat` now drives pi via the `@mariozechner/pi-coding-agent` SDK through a small Node.js process at `priv/pi-bridge/bridge.js`. Every pi call routes through a JSON-lines protocol on stdin/stdout, enabling a structured event stream (text deltas, tool-execution start/end, thinking, agent end) and clean lifecycle management. See [`docs/pi-bridge.md`](docs/pi-bridge.md) for the design note.
- First-run install: the bridge's Node dependencies install automatically on first use via `npm ci`, with an `.install-marker` that short-circuits subsequent runs.
- `FDR_BRIDGE_DIR` env var to point at a local bridge checkout during development.
- Unit test suite for the bridge client and installer (`tests/test_pi_bridge.py`, `tests/test_bridge_install.py`), plus an integration test (`tests/integration/test_bridge_end_to_end.py`) gated on Node.js availability and run via `pytest -m integration`.

### Changed

- Pi invocation no longer uses the `pi` CLI subprocess. `PiRunner.run_pi` translates the historical argv (`-p`, `--tools`, `--model`, `@file`) into structured bridge commands. The `(returncode, stdout, stderr)` return contract is preserved so managers don't change.
- `fix-die-repeat` commits to pi as the single backend. Multi-backend scaffolding (paused issues #16 / #17 / #20) is no longer on the roadmap — the sidecar bridge replaces the need for a separate backend-abstraction layer.
- The `--model-skip` fallback on 503 capacity errors is no longer automatic. The bridge exposes `set_model` but has no fallback list; the 503 handler now logs a warning and retries with the same model. Users who relied on pi's model cycling can configure `FDR_MODEL` / `--model` explicitly.
- CI workflow and `scripts/ci.sh` now set up Node.js 20 and run `npm ci` for the bridge before running Python tests.

### Removed

- Direct pi CLI subprocess path in `PiRunner.run_pi`. See the [pi-bridge design note](docs/pi-bridge.md) for the replacement architecture.

### Notes

- Windows support for the sidecar bridge is **unverified**. Linux and macOS are exercised. PRs welcome.
- Users who customized the bridge location can set `FDR_BRIDGE_DIR=/absolute/path`.
