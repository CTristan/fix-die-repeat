# pi-bridge — design decision note

Part of umbrella [#28](https://github.com/CTristan/fix-die-repeat/issues/28) Phase 0 ([#33](https://github.com/CTristan/fix-die-repeat/issues/33)).

## Purpose

`fix-die-repeat` currently invokes `pi` as a CLI subprocess — every call goes through `PiRunner.run_pi(*args)` in `runner.py`, which shells out `["pi", *args]`, captures stdout/stderr, and returns a `(returncode, stdout, stderr)` tuple. The verdict, when one is emitted, arrives via a file that `pi` wrote during the run.

Phase 0 replaces that transport with a **Node.js sidecar** that imports `@mariozechner/pi-coding-agent` and its peer packages as libraries and talks to `fix-die-repeat` over a JSON-lines protocol on stdin/stdout. No prompts, templates, or verdict-parsing logic change here; Phase 0 is a pure transport substitution with behavior preservation. The structured verdict contract lands in Phase 1 ([#29](https://github.com/CTristan/fix-die-repeat/issues/29)) on top of this foundation.

## Why a sidecar (not the CLI, not `pi --mode rpc`)

**Pi CLI** marshals every invocation through argv + on-disk files. That shape pushed fdr into today's fragile contract — `"NO_ISSUES"` as a literal-match pass marker, `[CRITICAL]` as a prompt-only severity token — because there was no other way to express structure across the subprocess boundary. It also hides the agent's internal events (tool calls, thinking, intermediate assistant text); fdr sees only the final stdout buffer.

**`pi --mode rpc`** does stream structured events and accepts a rich command set (`prompt`, `steer`, `set_model`, `compact`, `fork`, `get_last_assistant_text`, etc.). It looked at first glance like a drop-in SDK. But the `RpcCommand` union has no `register_tool` command — verified in `node_modules/@mariozechner/pi-coding-agent/dist/modes/rpc/rpc-types.d.ts`. Custom tools in `pi` live in TypeScript extensions loaded via `-e path/to/ext.js`; the RPC protocol is for session control and event streaming, not schema-enforced tool definitions emitted from a Python client. So RPC gives us a fancier subprocess wrapper but not the schema-enforcement-at-emit-time win that motivated the pivot.

**The sidecar pattern** — a Node.js subprocess that imports `pi` as a library — sidesteps both problems. The bridge has full access to the pi SDK (`createAgentSession`, tool factories, event subscription). Python stays in Python; the bridge handles TypeScript-side concerns. This pattern is battle-tested in the sibling `containment-loop` project at `~/projects/containment-loop/priv/pi-sdk-bridge/bridge.js` (~540 LOC of production code driving an Elixir/Phoenix orchestration server).

Notably, even with full SDK access, Containment Loop's verdict parsing is still regex-over-final-text, not a registered `submit_verdict` tool — schema-enforced tool registration is possible in principle but requires maintaining a separate TypeScript tool definition alongside the bridge, and the juice wasn't worth the squeeze for their use case. fdr inherits that judgement. The Phase 1 ([#29](https://github.com/CTristan/fix-die-repeat/issues/29)) structured-verdict work uses Pydantic validation over the agent's final text; the bridge's role is to deliver that text cleanly with observable events along the way, not to impose a wire-level schema on the agent.

## Architecture

Two processes, one bridge per `fix-die-repeat` invocation:

```
fix-die-repeat (Python)                        pi-bridge (Node.js)
  PiRunner                                       bridge.js
    ├── ensure_bridge_installed()                  ├── import { createAgentSession, ... }
    ├── PiBridge (context manager)  ──stdin──→    ├── readline loop on stdin
    │     prompt / set_model /                    ├── dispatch command
    │     compact / abort / shutdown              ├── drive pi session
    │                                ←──stdout──  ├── emit events
    │                                              │     ready / text_delta /
    │                                              │     thinking_delta /
    │                                              │     tool_execution_* /
    │                                              │     tool_blocked /
    │                                              │     agent_end / error
    └── run_pi / run_pi_safe /
        emergency_compact → bridge.*
```

One bridge process spans the entire `fix-die-repeat` run. Each pi invocation is a fresh session — we send `new_session` between prompts so reviews / fixes / introspection runs don't accumulate context from one another. This matches how the CLI path works today (each `pi` invocation is a new process, therefore a new session).

## Protocol

JSON objects, one per line, UTF-8, `\n`-terminated. Unknown fields are ignored on both sides for forward compatibility.

### Commands (Python → bridge, stdin)

| Command | Shape | Purpose |
|---|---|---|
| `init` | `{type, model, provider, tools, workingDir, thinking?}` | Initial handshake; bridge constructs an `AgentSession`. Bridge replies with `ready` when the session is ready. Sent once per bridge lifetime. |
| `prompt` | `{type, message, timeoutMs?}` | Run one agent turn in a fresh session. Bridge emits events during the run and `agent_end` at the end with the final assistant text. |
| `set_model` | `{type, provider, modelId}` | Swap model mid-run (capacity-fallback path, currently `pi -p /model-skip`). |
| `compact` | `{type}` | Emergency context compaction (currently `PiRunner.emergency_compact`). |
| `abort` | `{type}` | Cancel the current agent turn. Bridge emits `error` with `reason: "aborted"`. |
| `shutdown` | `{type}` | Graceful stop. Bridge finishes any in-flight turn, then exits with code 0. |

### Events (bridge → Python, stdout)

| Event | Shape | When |
|---|---|---|
| `ready` | `{type}` | Emitted once after `init` completes. |
| `text_delta` | `{type, delta, messageId?}` | Streaming assistant text. Logged for visibility; Python does not need to consume. |
| `thinking_delta` | `{type, delta, messageId?}` | Streaming hidden reasoning (if the model emits it). |
| `tool_execution_start` | `{type, toolCallId, toolName, args}` | Emitted before a tool runs. |
| `tool_execution_end` | `{type, toolCallId, toolName, result, isError}` | Emitted after a tool completes. |
| `tool_blocked` | `{type, toolCallId, toolName, pattern}` | Emitted when a deny-pattern prevents a tool from running (not used by Phase 0; reserved for future security-rail work). |
| `agent_end` | `{type, finalText, messages, stats?}` | Terminal event for a `prompt` command. `finalText` is the final assistant message text; `messages` is the full turn transcript; `stats` carries token counts when the SDK surfaces them. |
| `error` | `{type, reason, detail?}` | Terminal event for any command that fails (including `abort`). |

Diagnostics (log lines, warnings, anything not a protocol event) go to stderr and are forwarded into `Paths.pi_log` on the Python side.

### Framing rules

- The bridge writes exactly one JSON object per stdout line, never split. Lines are UTF-8.
- The Python side reads line-by-line; any non-JSON line is forwarded to the logger as a bridge warning and the read loop continues. The bridge should not emit non-JSON on stdout, but a robust reader is cheap.
- Either side closing stdin/stdout terminates the bridge.

## Command mapping

Every current pi invocation has a structured equivalent:

| Today | Tomorrow |
|---|---|
| `run_pi("-p", prompt)` | `bridge.prompt(prompt)` |
| `run_pi("-p", "/model-skip")` | `bridge.set_model(provider, modelId)` — the Python side picks the next fallback from `Settings` and passes both fields explicitly. |
| `emergency_compact()` | `bridge.compact()` |
| `run_pi("-p", "/abort")` (unused today) | `bridge.abort()` |
| any argv-based tool flag (`--tools read,edit,...`) | set at `init` once; no per-prompt override for Phase 0. |

`PiBridge.prompt()` returns `(returncode, stdout, stderr)` to match the existing `run_pi` contract — `stdout` is `agent_end.finalText`, `stderr` is bridge diagnostics for the run, `returncode` is 0 on `agent_end` and 1 on `error` or timeout. Call sites in the runner / managers keep their existing signatures; only `PiRunner.run_pi` itself knows the transport changed.

## Authentication

The bridge inherits process environment. `pi` already reads `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ZAI_API_KEY`, and OAuth credentials from its default config directory (`~/.pi/`). Whatever auth works for `pi` on the command line works for the bridge. `fix-die-repeat` does **not** introduce its own credential store — no `auth.json`, no secret provisioning. Users configure `pi` once; `fix-die-repeat` benefits transitively.

This is a deliberate simplification versus Containment Loop, which maintains its own `auth.json` because it has to orchestrate OAuth flows from a headless server. `fix-die-repeat` is a local CLI in an interactive shell; the user is present to run `pi`'s own auth setup if needed.

## Version pinning

`priv/pi-bridge/package.json` pins exact versions of the three pi packages:

- `@mariozechner/pi-coding-agent`
- `@mariozechner/pi-agent-core`
- `@mariozechner/pi-ai`

Initial pin: the latest stable version verified against `fix-die-repeat`'s existing test suite at implementation time. Today that's `0.67.68` (what the globally-installed pi reports). Update cadence is manual: when `pi` releases a version with features `fix-die-repeat` wants, bump the pin deliberately, re-run the full test suite, and note the bump in `CHANGELOG.md`. No automatic tracking.

This pinning philosophy matches Containment Loop (they're on `0.64.0`, deliberately behind current). Locking the version guarantees reproducible builds and prevents surprise breakage when `pi` rev's its `AgentSession` API. A `package-lock.json` is committed alongside `package.json` so `npm ci` reproduces the exact tree on every machine.

## Install story

The bridge's `node_modules/` is **not** bundled in the Python wheel. The wheel ships only `bridge.js` + `package.json` + `package-lock.json` under `priv/pi-bridge/`. When the runner enters its context manager, `PiRunner.__enter__` calls `ensure_bridge_installed()`, which:

1. Checks for `priv/pi-bridge/node_modules/.install-marker` (a sentinel file with the package version). If present and matches the expected version, returns immediately.
2. If missing: runs `npm ci` in `priv/pi-bridge/`. Requires `node` and `npm` on PATH. `npm ci` uses the checked-in lockfile for deterministic resolution; first-run cost is the npm download (~seconds on a warm network).
3. On success, writes `.install-marker` with the current package version. Subsequent runs skip the install.
4. On failure — `node` or `npm` missing — raises `BridgeInstallError` with actionable text: *"fix-die-repeat requires Node.js ≥20 for the pi bridge. Install Node.js (via Homebrew, nvm, or https://nodejs.org) and re-run."*

The marker-file approach keeps the wheel small, avoids bundling platform-specific binaries (pi has native dependencies), and makes the install step legible (one `npm ci`, documented in README).

For development, `FDR_BRIDGE_DIR` can override the bridge location — pointing at a local-checkout bridge so developers can iterate on `bridge.js` without rebuilding the wheel.

## Cross-platform

Phase 0 is verified on macOS and Linux. Windows is unverified — pi itself runs on Windows (Node.js is portable), but `subprocess.Popen` + stdio pipes have subtly different semantics there, and `fix-die-repeat`'s test suite hasn't been exercised on Windows. `CHANGELOG.md` documents this explicitly: *"Windows support unverified; PRs welcome."*

## Test strategy

- **Unit tests for `PiBridge`** — `tests/test_pi_bridge.py` mocks the subprocess lifecycle and synthesizes JSONL event streams. Covers: `__enter__`/`__exit__` resource management, `prompt()` returning the right tuple on `agent_end`, `prompt()` returning non-zero on `error`, timeout handling, malformed-JSONL tolerance, `set_model`/`compact`/`abort` command framing.
- **Unit tests for install** — `tests/test_bridge_install.py` mocks `subprocess.run` and `shutil.which` to cover the three paths: marker present (skip), marker absent (install), node missing (actionable error).
- **Migration tests** — every existing test that patches `utils.run_command` for pi calls gets retargeted to patch `PiBridge.prompt`. The tuple API preservation means assertion shapes mostly don't change.
- **Integration test** — `tests/integration/test_bridge_end_to_end.py` spawns a real bridge subprocess and sends a canned prompt. Gated by `shutil.which("node")`; skipped otherwise. Marked with `@pytest.mark.integration` and excluded from default `pytest` runs to keep the unit suite fast and offline.
- **What's out of scope** — actual model calls. The integration test uses a dummy prompt and doesn't require credentials; real LLM round-trips stay in dev-mode manual verification (`uv run fix-die-repeat --debug`).

## Failure modes and handling

| Failure | Detection | Handling |
|---|---|---|
| Bridge fails to spawn (Node missing at runtime, corrupted install) | `subprocess.Popen` raises / exits immediately | `PiBridge.__enter__` raises a wrapped `BridgeError` with stderr diagnostics; `PiRunner` treats as a fatal config error. |
| Bridge crashes mid-prompt | `Popen.poll()` non-None during event loop | `prompt()` returns `(1, "", stderr_buffer)`; `run_pi_safe` retries via `bridge.abort()` + new `prompt()`. |
| Malformed JSONL line | `json.loads` raises | Logged as warning via `self.logger`; read loop continues. Bridge shouldn't emit these, but we don't die on one. |
| Pi goes silent mid-prompt | No event arrives within `FDR_PI_IDLE_TIMEOUT_S` (default 120s) | `prompt()` returns `(1, "", "…idle for more than Ns")`; `run_pi_safe` retries once. Any bridge event (tool call, text delta, thinking delta) resets the idle timer, so long but active turns don't trip it. |
| Runaway event storm | `FDR_PI_HARD_TIMEOUT_S` (default 3600s) wall-clock cap | `prompt()` returns `(1, "", "…exceeded hard timeout")`; safety net for a bridge that keeps the idle timer alive but never emits `agent_end`. |
| Credential missing | pi SDK error surfaced as `error` event during `init` | `PiBridge.__enter__` raises with actionable text: *"pi reports missing credentials for provider X. Run `pi` interactively once to complete auth."* |
| Orphan bridge on fdr crash | pi bridge detects stdin EOF | `bridge.js` wires `process.stdin.on("close", () => process.exit(0))`; Python side sends `shutdown` in `__exit__`. Belt-and-suspenders. |

## Future evolution (informational; not Phase 0 scope)

- **Phase 1 ([#29](https://github.com/CTristan/fix-die-repeat/issues/29))** — structured verdict contract on top of this transport. Pydantic models, three-tier severity, 8-category taxonomy. The verdict parsing shifts from "read the file pi wrote" to "Pydantic-validate the final text from `agent_end`." File-writing via pi's `write` tool continues for debugging artifacts but is no longer load-bearing.
- **Phase 2a ([#31](https://github.com/CTristan/fix-die-repeat/issues/31))** — evidence requirement for dynamic-behavior claims. The event stream (`tool_execution_start/end`) makes it possible to verify the agent actually ran the code it claims to have verified.
- **Phase 2b ([#30](https://github.com/CTristan/fix-die-repeat/issues/30))** — termination model. Could consume `stats` from `agent_end` and event-rate signals from the stream.
- **Phase 3 ([#32](https://github.com/CTristan/fix-die-repeat/issues/32))** — feedback loop for `lang_check_gap`. Deny-pattern telemetry (`tool_blocked`) could feed a repo-specific denylist learned over time.
- **Deny patterns as security rails** (R7 in the umbrella, currently parked) — the bridge's tool-blocked event gives us a natural hook for per-segment regex rails à la Containment Loop's `deny_patterns`. Not scoped for Phase 0 but the protocol leaves room for it.

## References

- Sibling implementation: [`containment-loop/priv/pi-sdk-bridge/bridge.js`](../../../containment-loop/priv/pi-sdk-bridge/bridge.js) and the Elixir client at [`containment-loop/lib/containment_loop/backend/pi_sdk/session.ex`](../../../containment-loop/lib/containment_loop/backend/pi_sdk/session.ex).
- Pi RPC type definitions (proof of the no-`register_tool` gap): `node_modules/@mariozechner/pi-coding-agent/dist/modes/rpc/rpc-types.d.ts`.
- Pi extension API (for the tool-call-schema path we're deliberately not taking here): `node_modules/@mariozechner/pi-coding-agent/dist/core/extensions/types.d.ts`.
- Pi SDK entry points (`createAgentSession`, tool factories): `node_modules/@mariozechner/pi-coding-agent/dist/core/sdk.js`.
