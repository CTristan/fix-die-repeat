/**
 * pi-bridge — Node.js sidecar for fix-die-repeat.
 *
 * Speaks JSON-lines over stdin/stdout with the Python PiBridge client.
 * Imports @mariozechner/pi-coding-agent as a library; no pi CLI involvement.
 *
 * Protocol (one JSON object per line):
 *
 *   Python → Bridge (stdin):
 *     {"type": "init", "model": "...", "provider": "...",
 *      "tools": ["read","bash","edit","write","grep","find","ls"],
 *      "workingDir": "/path", "thinking": "medium"}
 *     {"type": "prompt", "message": "...", "timeoutMs": 300000,
 *      "tools": ["read","edit"], "provider": "...", "modelId": "..."}
 *     {"type": "set_model", "provider": "...", "modelId": "..."}
 *     {"type": "compact"}
 *     {"type": "abort"}
 *     {"type": "shutdown"}
 *
 *   Bridge → Python (stdout):
 *     {"type": "ready"}
 *     {"type": "text_delta", "delta": "...", "messageId": "..."}
 *     {"type": "thinking_delta", "delta": "...", "messageId": "..."}
 *     {"type": "tool_execution_start", "toolCallId": "...", "toolName": "...", "args": {...}}
 *     {"type": "tool_execution_end", "toolCallId": "...", "toolName": "...", "result": "...", "isError": false}
 *     {"type": "agent_end", "finalText": "...", "messages": [...], "stats": {...}}
 *     {"type": "error", "reason": "...", "detail": "..."}
 *
 * Diagnostics (non-protocol) go to stderr.
 */

import { createAgentSession } from "@mariozechner/pi-coding-agent";
import {
  createReadTool,
  createBashTool,
  createEditTool,
  createWriteTool,
  createGrepTool,
  createFindTool,
  createLsTool,
} from "@mariozechner/pi-coding-agent";
import { getModel } from "@mariozechner/pi-ai";
import { createInterface } from "node:readline";

// --- Config state (populated at init) ---

let config = null;
let activeSession = null;

// --- Tool factory registry ---

const TOOL_FACTORIES = {
  read: createReadTool,
  bash: createBashTool,
  edit: createEditTool,
  write: createWriteTool,
  grep: createGrepTool,
  find: createFindTool,
  ls: createLsTool,
};

// --- JSONL I/O ---

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function diag(msg) {
  process.stderr.write(`[pi-bridge] ${msg}\n`);
}

function emitError(reason, detail) {
  emit({ type: "error", reason, detail: detail ?? null });
}

// --- Command dispatcher ---

async function dispatch(cmd) {
  try {
    switch (cmd.type) {
      case "init":
        return await handleInit(cmd);
      case "prompt":
        return await handlePrompt(cmd);
      case "set_model":
        return handleSetModel(cmd);
      case "compact":
        return handleCompact();
      case "abort":
        return handleAbort();
      case "shutdown":
        return handleShutdown();
      default:
        emitError("unknown_command", `Unknown command type: ${cmd.type}`);
    }
  } catch (err) {
    emitError("command_failed", `${cmd.type} failed: ${err?.message ?? String(err)}`);
  }
}

// --- Commands ---

async function handleInit(cmd) {
  if (config) {
    emitError("already_initialized", "init was called twice");
    return;
  }
  const { model, provider, tools, workingDir, thinking } = cmd;
  // model and provider are optional — pi picks from settings when both are absent.
  if ((model && !provider) || (!model && provider)) {
    emitError("init_missing_fields", "init requires both model and provider, or neither");
    return;
  }
  config = {
    model: model ?? null,
    provider: provider ?? null,
    tools: Array.isArray(tools) && tools.length > 0 ? tools : ["read", "bash", "edit", "write"],
    workingDir: workingDir ?? process.cwd(),
    thinking: thinking ?? "medium",
  };
  diag(
    `init: provider=${provider ?? "(default)"} model=${model ?? "(default)"} ` +
      `tools=${config.tools.join(",")} cwd=${config.workingDir}`,
  );
  emit({ type: "ready" });
}

async function handlePrompt(cmd) {
  if (!config) {
    emitError("not_initialized", "init must be called before prompt");
    return;
  }
  const { message, timeoutMs, tools: toolsOverride, provider: providerOverride, modelId: modelOverride } = cmd;
  if (typeof message !== "string" || message.length === 0) {
    emitError("prompt_missing_message", "prompt requires a non-empty message");
    return;
  }

  // Per-prompt model override mirrors legacy `pi -p --model X`: one-shot, does
  // not mutate bridge config. When absent, fall back to init-time defaults.
  if ((providerOverride && !modelOverride) || (!providerOverride && modelOverride)) {
    emitError(
      "prompt_model_override_partial",
      "prompt requires both provider and modelId, or neither",
    );
    return;
  }
  const effectiveProvider = providerOverride ?? config.provider;
  const effectiveModel = modelOverride ?? config.model;

  // Resolve model object if configured; otherwise let pi pick from settings.
  let modelObj;
  if (effectiveProvider && effectiveModel) {
    try {
      modelObj = getModel(effectiveProvider, effectiveModel);
    } catch (err) {
      emitError(
        "model_resolution_failed",
        `getModel(${effectiveProvider}, ${effectiveModel}) failed: ${err?.message ?? String(err)}`,
      );
      return;
    }
  }

  // Build tool instances — use per-prompt override if provided
  const toolNames = Array.isArray(toolsOverride) && toolsOverride.length > 0 ? toolsOverride : config.tools;
  const toolInstances = [];
  for (const name of toolNames) {
    const factory = TOOL_FACTORIES[name];
    if (!factory) {
      emitError("unknown_tool", `Unknown tool: ${name}`);
      return;
    }
    toolInstances.push(factory());
  }

  // Fresh session per prompt — task isolation
  let session;
  try {
    const sessionOpts = {
      cwd: config.workingDir,
      thinkingLevel: config.thinking,
      tools: toolInstances,
    };
    if (modelObj) sessionOpts.model = modelObj;
    const result = await createAgentSession(sessionOpts);
    session = result.session;
  } catch (err) {
    emitError("session_creation_failed", err?.message ?? String(err));
    return;
  }

  activeSession = session;

  const finalTextFragments = [];
  const turnMessages = [];

  const unsubscribe = session.subscribe((event) => {
    switch (event.type) {
      case "text_delta":
        if (event.delta) finalTextFragments.push(event.delta);
        emit({ type: "text_delta", delta: event.delta, messageId: event.messageId });
        break;
      case "thinking_delta":
        emit({ type: "thinking_delta", delta: event.delta, messageId: event.messageId });
        break;
      case "tool_call":
      case "tool_execution_start":
        emit({
          type: "tool_execution_start",
          toolCallId: event.toolCallId ?? event.id,
          toolName: event.toolName ?? event.name,
          args: event.args ?? event.arguments ?? null,
        });
        break;
      case "tool_result":
      case "tool_execution_end":
        emit({
          type: "tool_execution_end",
          toolCallId: event.toolCallId ?? event.id,
          toolName: event.toolName ?? event.name,
          result: typeof event.result === "string" ? event.result : JSON.stringify(event.result ?? null),
          isError: Boolean(event.isError),
        });
        break;
      case "message_end":
      case "message_update":
        if (event.message) turnMessages.push(event.message);
        break;
      default:
        // Other events (queue_update, compaction_*, auto_retry_*, etc.) ignored for Phase 0.
        break;
    }
  });

  // Timeout watchdog
  const timeout = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 300_000;
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    try { session.abort(); } catch { /* swallow */ }
  }, timeout);

  try {
    await session.prompt(message, { streamingBehavior: "followUp" });
    clearTimeout(timer);
    unsubscribe();
    activeSession = null;

    if (timedOut) {
      emitError("timeout", `prompt exceeded ${timeout}ms`);
      return;
    }

    const finalText = finalTextFragments.join("");
    emit({
      type: "agent_end",
      finalText,
      messages: turnMessages,
      stats: null,
    });
  } catch (err) {
    clearTimeout(timer);
    unsubscribe();
    activeSession = null;
    if (timedOut) {
      emitError("timeout", `prompt exceeded ${timeout}ms`);
      return;
    }
    emitError("prompt_failed", err?.message ?? String(err));
  }
}

function handleSetModel(cmd) {
  if (!config) {
    emitError("not_initialized", "init must be called before set_model");
    return;
  }
  const { provider, modelId } = cmd;
  if (!provider || !modelId) {
    emitError("set_model_missing_fields", "set_model requires provider and modelId");
    return;
  }
  config.provider = provider;
  config.model = modelId;
  diag(`set_model: provider=${provider} model=${modelId}`);
  emit({ type: "ready" });
}

function handleCompact() {
  // With fresh-session-per-prompt semantics, there is no cross-prompt state to compact.
  // Kept for protocol compatibility with fdr's emergency-compact path.
  diag("compact: no-op (fresh-session-per-prompt semantics)");
  emit({ type: "ready" });
}

function handleAbort() {
  if (activeSession) {
    try {
      activeSession.abort();
      diag("abort: signaled active session");
    } catch (err) {
      diag(`abort failed: ${err?.message ?? String(err)}`);
    }
  }
}

function handleShutdown() {
  diag("shutdown requested");
  if (activeSession) {
    try { activeSession.abort(); } catch { /* swallow */ }
  }
  process.exit(0);
}

// --- Main loop ---

const rl = createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

// Serialize command handling: chain each dispatch onto the previous one so a
// second command (e.g., a `prompt` arriving while the first is still running)
// cannot interleave and corrupt shared state like `activeSession` or `config`.
// The trailing `.catch` keeps the chain alive if a dispatch ever rejects — the
// Python side already enforces single-in-flight, so this is belt-and-suspenders.
let commandQueue = Promise.resolve();

rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  let cmd;
  try {
    cmd = JSON.parse(trimmed);
  } catch (err) {
    emitError("malformed_command", `Not JSON: ${trimmed.slice(0, 200)}`);
    return;
  }
  commandQueue = commandQueue
    .then(() => dispatch(cmd))
    .catch((err) => {
      emitError(
        "unhandled_rejection",
        err?.stack ?? err?.message ?? String(err),
      );
    });
});

rl.on("close", () => {
  diag("stdin closed; exiting");
  process.exit(0);
});

process.on("SIGTERM", () => {
  diag("SIGTERM received; exiting");
  process.exit(0);
});

process.on("SIGINT", () => {
  diag("SIGINT received; exiting");
  process.exit(0);
});

process.on("uncaughtException", (err) => {
  emitError("uncaught_exception", err?.stack ?? err?.message ?? String(err));
  process.exit(1);
});

process.on("unhandledRejection", (err) => {
  emitError("unhandled_rejection", err?.stack ?? err?.message ?? String(err));
  process.exit(1);
});

diag("pi-bridge started; awaiting init on stdin");
