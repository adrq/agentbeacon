// Redirect console to stderr before any imports — SDK or transitive deps
// may call console.log which would corrupt the JSON Lines protocol on stdout.
console.log = (...args: unknown[]) => console.error(...args);
console.info = (...args: unknown[]) => console.error(...args);
console.warn = (...args: unknown[]) => console.error(...args);
console.debug = (...args: unknown[]) => console.error(...args);

import * as readline from "node:readline";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as crypto from "node:crypto";
import type {
  Command,
  StartCommand,
  Part,
  McpServerConfig,
} from "./common/protocol.js";
import type {
  CopilotClient as CopilotClientType,
  CopilotSession,
  SessionEventPayload,
} from "@github/copilot-sdk";

const { CopilotClient } = (await import("@github/copilot-sdk")) as {
  CopilotClient: typeof CopilotClientType;
};
import { emit } from "./common/stdio-bridge.js";

// Orchestration tools that bypass AgentBeacon's coordination layer.
// Blocked via excludedTools in createSession/resumeSession config.
const EXCLUDED_ORCHESTRATION_TOOLS: string[] = [
  "task", // Subagent spawning
  "read_agent", // Delegate to named agents
  "list_agents", // Enumerate delegation targets
];

type TurnState = "idle" | "starting" | "in-flight";

// --- Command queue (single stdin listener, cancel as side-effect) ---

let currentSession: CopilotSession | null = null;
let aborted = false;
let stoppedByUser = false;
let pendingStopTurn = false;
let turnState: TurnState = "idle";
let pendingBlocks: Record<string, unknown>[] = [];

function hasQueuedTurnCommand(): boolean {
  return commandQueue.some(
    (cmd) => cmd.type === "start" || cmd.type === "prompt",
  );
}

let shutdownRequested = false;

function discardQueuedTurnCommands(): void {
  const retained = commandQueue.filter(
    (cmd) => cmd.type !== "start" && cmd.type !== "prompt",
  );
  commandQueue.length = 0;
  commandQueue.push(...retained);
}

function flushPendingBlocks(): void {
  if (pendingBlocks.length > 0) {
    emit({
      type: "message",
      role: "assistant",
      content: [...pendingBlocks],
    });
    pendingBlocks = [];
  }
}

const commandQueue: Command[] = [];
let queueResolve: (() => void) | null = null;

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  let cmd: Command;
  try {
    cmd = JSON.parse(line);
  } catch {
    process.stderr.write(`ignoring malformed stdin line, len=${line.length}\n`);
    return;
  }
  if (cmd.type === "cancel" && currentSession && turnState !== "idle") {
    aborted = true;
    currentSession.abort();
  } else if (cmd.type === "cancel") {
    // Cancel received while idle — shut down cleanly.
    shutdownRequested = true;
    discardQueuedTurnCommands();
    commandQueue.push({ type: "stop" } as Command);
    if (queueResolve) {
      queueResolve();
      queueResolve = null;
    }
  } else if (cmd.type === "stop_turn") {
    // Buffered locally until the worker can deliver it.
    if (currentSession && turnState !== "idle") {
      stoppedByUser = true;
      aborted = true;
      discardQueuedTurnCommands();
      currentSession.abort();
    } else if (turnState === "starting" || hasQueuedTurnCommand()) {
      pendingStopTurn = true;
    } else {
      process.stderr.write(`[copilot] ignoring stale stop_turn while idle\n`);
    }
  } else if (
    cmd.type === "prompt" &&
    turnState === "in-flight" &&
    currentSession
  ) {
    // Mid-turn: forward directly to SDK for immediate steering.
    try {
      const sendOpts = partsToSendOptions(cmd.parts);
      currentSession
        .send({
          prompt: sendOpts.prompt,
          attachments: sendOpts.attachments,
          mode: "immediate",
        })
        .catch((e: unknown) => {
          process.stderr.write(`[copilot] mid-turn send rejected: ${e}\n`);
        });
      emit({ type: "accepted" });
    } catch (e: unknown) {
      process.stderr.write(`[copilot] mid-turn send threw: ${e}\n`);
    }
  } else {
    commandQueue.push(cmd);
    if (queueResolve) {
      queueResolve();
      queueResolve = null;
    }
  }
});
rl.on("close", () => {
  commandQueue.push({ type: "eof" });
  if (queueResolve) {
    queueResolve();
    queueResolve = null;
  }
});

async function nextCommand(): Promise<Command> {
  while (commandQueue.length === 0) {
    await new Promise<void>((r) => {
      queueResolve = r;
    });
  }
  return commandQueue.shift()!;
}

// --- Session runner ---

// Errors that mean the session cannot continue.
const FATAL_ERROR_TYPES = new Set(["connection_closed", "auth_failure"]);

/**
 * Wait for the session to become idle (turn complete).
 * Only fatal errors (connection loss, auth failure) reject early;
 * recoverable errors are logged while we keep waiting.
 */
function waitForIdle(session: CopilotSession): {
  promise: Promise<void>;
  cancel: () => void;
} {
  let resolve: () => void;
  let reject: (err: Error) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });

  const unsubIdle = session.on("session.idle", () => {
    cleanup();
    resolve();
  });

  const unsubError = session.on(
    "session.error",
    (event: SessionEventPayload<"session.error">) => {
      if (FATAL_ERROR_TYPES.has(event.data.errorType)) {
        cleanup();
        reject(new Error(event.data.message));
      }
      // Recoverable errors are logged by the separate session.error handler
      // and we continue waiting for session.idle.
    },
  );

  let cleaned = false;
  function cleanup() {
    if (cleaned) return;
    cleaned = true;
    unsubIdle();
    unsubError();
  }

  function cancel() {
    cleanup();
    resolve();
  }

  return { promise, cancel };
}

// --- Parts → Copilot send options ---

function mimeToExt(mimeType: string): string {
  const map: Record<string, string> = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
  };
  return map[mimeType] ?? ".bin";
}

function partsToSendOptions(parts: Part[]): {
  prompt: string;
  attachments?: { type: "file"; path: string; displayName?: string }[];
  _tempFiles?: string[];
} {
  const textParts = parts.filter(
    (p): p is Part & { text: string } => "text" in p,
  );
  let prompt = textParts.map((p) => (p as { text: string }).text).join("\n");
  const fileParts = parts.filter(
    (p): p is Part & { raw: string } =>
      "raw" in p && !!(p as { raw: string }).raw,
  );
  if (!prompt.trim() && fileParts.length > 0) prompt = "[see attached]";
  if (fileParts.length === 0) return { prompt };

  const tempFiles: string[] = [];
  const attachments = fileParts.map((p) => {
    const raw = p as { raw: string; mediaType?: string; filename?: string };
    const ext = mimeToExt(raw.mediaType ?? "application/octet-stream");
    const tmpPath = path.join(os.tmpdir(), `ab-${crypto.randomUUID()}${ext}`);
    fs.writeFileSync(tmpPath, Buffer.from(raw.raw, "base64"));
    tempFiles.push(tmpPath);
    return { type: "file" as const, path: tmpPath, displayName: raw.filename };
  });
  return { prompt, attachments, _tempFiles: tempFiles };
}

function cleanupTempFiles(paths?: string[]): void {
  if (!paths) return;
  for (const p of paths) {
    try {
      fs.unlinkSync(p);
    } catch {
      /* best effort */
    }
  }
}

async function runSession(startCmd: StartCommand): Promise<void> {
  turnState = "starting";
  const client = new CopilotClient({
    useStdio: true,
    logLevel: "error",
  });
  await client.start();

  try {
    // Build MCP servers config, ensuring tools field is always present
    const mcpServers = startCmd.mcpServers
      ? buildMcpServers(startCmd.mcpServers)
      : undefined;

    const sessionConfig: Record<string, unknown> = {
      streaming: true,
      onPermissionRequest: async (
        request: { kind: string },
        _invocation: { sessionId: string },
      ) => {
        process.stderr.write(
          `[copilot] auto-approving permission: ${request.kind}\n`,
        );
        return { kind: "approved" as const };
      },
      excludedTools: EXCLUDED_ORCHESTRATION_TOOLS,
    };

    if (startCmd.model) sessionConfig.model = startCmd.model;
    if (startCmd.systemPrompt) {
      sessionConfig.systemMessage = {
        mode: "append",
        content: startCmd.systemPrompt,
      };
    }
    if (startCmd.provider) sessionConfig.provider = startCmd.provider;
    if (startCmd.cwd) sessionConfig.workingDirectory = startCmd.cwd;
    if (mcpServers) sessionConfig.mcpServers = mcpServers;
    if (startCmd.reasoningEffort)
      sessionConfig.reasoningEffort = startCmd.reasoningEffort;

    let session: CopilotSession;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- dynamic config built at runtime
    const typedConfig = sessionConfig as any;
    if (startCmd.resumeSessionId) {
      session = await client.resumeSession(
        startCmd.resumeSessionId,
        typedConfig,
      );
    } else {
      session = await client.createSession(typedConfig);
    }
    currentSession = session;

    emit({
      type: "init",
      sessionId: session.sessionId,
    });

    // Reset pending blocks for this session. Tool blocks are buffered
    // until the next assistant.message, then flushed together so each
    // message event carries the structured parts that preceded it.
    // Reasoning blocks are emitted immediately (not buffered).
    pendingBlocks = [];
    let lastAssistantContent: string | undefined;

    session.on(
      "assistant.message",
      (event: SessionEventPayload<"assistant.message">) => {
        lastAssistantContent = event.data.content;
        pendingBlocks.push({ type: "text", text: event.data.content });
        flushPendingBlocks();
      },
    );

    session.on(
      "tool.execution_start",
      (event: SessionEventPayload<"tool.execution_start">) => {
        process.stderr.write(`[copilot] tool start: ${event.data.toolName}\n`);
        const block: Record<string, unknown> = {
          type: "tool_use",
          id: event.data.toolCallId,
          name: event.data.toolName,
        };
        if (event.data.arguments !== undefined) {
          block.input = event.data.arguments;
        }
        pendingBlocks.push(block);
      },
    );
    session.on(
      "tool.execution_complete",
      (event: SessionEventPayload<"tool.execution_complete">) => {
        process.stderr.write(
          `[copilot] tool complete: ${event.data.toolCallId}\n`,
        );
        let resultContent: string | unknown[];
        if (
          event.data.result?.contents &&
          event.data.result.contents.length > 0
        ) {
          // Preserve structured content (terminal output, images, etc.)
          // Available on both success and failure (e.g. bash exit-code 1).
          resultContent = event.data.result.contents;
        } else if (!event.data.success) {
          resultContent =
            event.data.error?.message ?? event.data.result?.content ?? "";
        } else {
          resultContent = event.data.result?.content ?? "";
        }
        pendingBlocks.push({
          type: "tool_result",
          tool_use_id: event.data.toolCallId,
          content: resultContent,
          is_error: !event.data.success,
        });
      },
    );

    session.on(
      "assistant.reasoning",
      (event: SessionEventPayload<"assistant.reasoning">) => {
        flushPendingBlocks();
        emit({
          type: "message",
          role: "assistant",
          content: [{ type: "thinking", thinking: event.data.content }],
        });
      },
    );

    session.on(
      "assistant.reasoning_delta",
      (event: SessionEventPayload<"assistant.reasoning_delta">) => {
        flushPendingBlocks();
        const text = event.data.deltaContent;
        if (typeof text === "string") {
          emit({
            type: "message",
            role: "assistant",
            content: [{ type: "thinking_delta", thinking: text }],
          });
        }
      },
    );

    session.on(
      "assistant.message_delta",
      (event: SessionEventPayload<"assistant.message_delta">) => {
        const text = event.data.deltaContent;
        if (typeof text === "string") {
          emit({
            type: "message",
            role: "assistant",
            content: [{ type: "text_delta", text }],
          });
        }
      },
    );

    // Log session errors — all are treated as fatal by waitForIdle(),
    // which rejects and lets emitTurnResult() emit the sole terminal event.
    session.on(
      "session.error",
      (event: SessionEventPayload<"session.error">) => {
        process.stderr.write(
          `[copilot] session error (${event.data.errorType}): ${event.data.message}\n`,
        );
      },
    );

    async function runTurn(parts: Part[]): Promise<void> {
      aborted = false;
      lastAssistantContent = undefined;
      turnState = "starting";

      const shouldStopTurn = pendingStopTurn;
      if (shouldStopTurn) {
        stoppedByUser = true;
        aborted = true;
        pendingStopTurn = false;
        turnState = "idle";
        emitTurnResult(undefined);
        return;
      }

      let fatalError: string | undefined;
      const idle = waitForIdle(session);
      const sendOpts = partsToSendOptions(parts);
      if (pendingStopTurn) {
        stoppedByUser = true;
        aborted = true;
        pendingStopTurn = false;
      }
      if (aborted) {
        turnState = "idle";
        idle.cancel();
        cleanupTempFiles(sendOpts._tempFiles);
        emitTurnResult(undefined);
        return;
      }
      turnState = "in-flight";
      try {
        await session.send({
          prompt: sendOpts.prompt,
          attachments: sendOpts.attachments,
        });

        // Signal worker that the SDK accepted the prompt.
        emit({ type: "accepted" });

        await idle.promise;
      } catch (e: unknown) {
        fatalError = String(e);
      } finally {
        turnState = "idle";
        idle.cancel();
        cleanupTempFiles(sendOpts._tempFiles);
      }

      if (fatalError && !aborted && !stoppedByUser) {
        process.stderr.write(`[copilot] fatal during turn: ${fatalError}\n`);
      }
      emitTurnResult(lastAssistantContent, fatalError);
    }

    // First turn
    {
      await runTurn(startCmd.parts);
    }

    // Multi-turn loop
    while (true) {
      const cmd = await nextCommand();
      if (cmd.type === "stop" || cmd.type === "eof") break;
      if (cmd.type === "start") {
        emit({
          type: "error",
          message: "Received start command during active session",
        });
        break;
      }
      if (cmd.type === "prompt") {
        await runTurn(cmd.parts);
      }
    }

    try {
      await session.disconnect();
    } catch {}
  } finally {
    turnState = "idle";
    currentSession = null;
    try {
      await client.stop();
    } catch {}
  }
}

function emitTurnResult(
  lastAssistantContent: string | undefined,
  fatalError?: string,
): void {
  flushPendingBlocks();
  if (stoppedByUser) {
    emit({
      type: "result",
      subtype: "stopped_by_user",
    });
    discardQueuedTurnCommands();
    stoppedByUser = false;
    pendingStopTurn = false;
    aborted = false;
  } else if (aborted) {
    emit({
      type: "result",
      subtype: "cancelled",
    });
  } else if (fatalError) {
    emit({
      type: "result",
      subtype: "error_during_execution",
      errors: [fatalError],
    });
  } else if (lastAssistantContent === undefined) {
    emit({
      type: "result",
      subtype: "error_during_execution",
      errors: ["Turn completed without an assistant message"],
    });
  } else {
    emit({
      type: "result",
      subtype: "success",
      result: lastAssistantContent,
    });
  }
  pendingStopTurn = false;
  aborted = false;
}

function buildMcpServers(
  servers: Record<string, McpServerConfig>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, config] of Object.entries(servers)) {
    const mapped = { ...config, tools: config.tools ?? ["*"] };
    // Copilot SDK uses "local" for stdio transport
    if (mapped.type === "stdio") mapped.type = "local";
    out[name] = mapped;
  }
  return out;
}

// --- Main loop ---

async function main(): Promise<void> {
  while (true) {
    const cmd = await nextCommand();
    if (cmd.type === "stop" || cmd.type === "eof") break;
    if (cmd.type !== "start") continue;

    try {
      await runSession(cmd);
    } catch (e: unknown) {
      emit({ type: "error", message: String(e) });
    }
    if (shutdownRequested) break;
  }

  // Emit terminal result so the scheduler knows this session is done.
  if (shutdownRequested) {
    emit({ type: "result", subtype: "cancelled" });
  }
}

main()
  .then(() => {
    rl.close();
  })
  .catch((e) => {
    process.stderr.write(`fatal: ${e}\n`);
    process.exit(1);
  });
