// Redirect console to stderr before any imports — SDK or transitive deps
// may call console.log which would corrupt the JSON Lines protocol on stdout.
console.log = (...args: unknown[]) => console.error(...args);
console.info = (...args: unknown[]) => console.error(...args);
console.warn = (...args: unknown[]) => console.error(...args);
console.debug = (...args: unknown[]) => console.error(...args);

import * as readline from "node:readline";
import { CopilotClient } from "@github/copilot-sdk";
import type { CopilotSession } from "@github/copilot-sdk";
import type { Command, StartCommand, Event, McpServerConfig } from "./common/protocol.js";
import { emit } from "./common/stdio-bridge.js";

// --- Command queue (single stdin listener, cancel as side-effect) ---

let currentSession: CopilotSession | null = null;
let aborted = false;
let pendingBlocks: Record<string, unknown>[] = [];

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
  if (cmd.type === "cancel" && currentSession) {
    aborted = true;
    currentSession.abort();
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

const FATAL_ERROR_CODES = new Set([
  "connection_closed",
  "auth_failure",
]);

async function runSession(startCmd: StartCommand): Promise<void> {
  const client = new CopilotClient({
    useStdio: true,
    autoRestart: true,
    logLevel: "error",
  });
  await client.start();

  try {
    // Build MCP servers config, ensuring tools field is always present
    const mcpServers = startCmd.mcpServers
      ? buildMcpServers(startCmd.mcpServers)
      : undefined;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
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

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const session = await client.createSession(sessionConfig as any);
    currentSession = session;

    emit({
      type: "init",
      sessionId: session.sessionId,
    });

    // Reset pending blocks for this session. Tool/reasoning blocks are
    // buffered until the next assistant.message, then flushed together
    // so each message event carries the structured parts that preceded it.
    pendingBlocks = [];

    session.on("assistant.message", (event) => {
      // Flush any accumulated tool/reasoning blocks before the text message
      pendingBlocks.push({ type: "text", text: event.data.content });
      emit({
        type: "message",
        role: "assistant",
        content: [...pendingBlocks],
      });
      pendingBlocks = [];
    });

    session.on("tool.execution_start", (event) => {
      process.stderr.write(
        `[copilot] tool start: ${event.data.toolName}\n`,
      );
      pendingBlocks.push({
        type: "tool_use",
        id: event.data.toolCallId,
        name: event.data.toolName,
      });
    });
    session.on("tool.execution_complete", (event) => {
      process.stderr.write(
        `[copilot] tool complete: ${event.data.toolCallId}\n`,
      );
    });

    session.on("assistant.reasoning", (event) => {
      pendingBlocks.push({ type: "thinking", thinking: event.data.content });
    });

    // Handle session errors — unknown/untyped errors are fatal,
    // only coded errors matching known recoverable patterns are logged.
    session.on("session.error", (event) => {
      const errorType = event.data.errorType;
      const message = event.data.message;
      if (!errorType || FATAL_ERROR_CODES.has(errorType)) {
        emit({ type: "error", message });
      } else {
        process.stderr.write(
          `[copilot] recoverable error (${errorType}): ${message}\n`,
        );
      }
    });

    // First turn
    aborted = false;
    let result = await session.sendAndWait({ prompt: startCmd.prompt });
    emitTurnResult(result);

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
        aborted = false;
        result = await session.sendAndWait({ prompt: cmd.text });
        emitTurnResult(result);
      }
    }

    await session.destroy();
  } finally {
    currentSession = null;
    await client.stop();
  }
}

function emitTurnResult(
  result: { data: { content: string } } | undefined,
): void {
  // Flush any accumulated tool/reasoning blocks that weren't followed by an assistant.message
  if (pendingBlocks.length > 0) {
    emit({
      type: "message",
      role: "assistant",
      content: [...pendingBlocks],
    });
    pendingBlocks = [];
  }
  if (result === undefined) {
    if (aborted) {
      emit({
        type: "result",
        subtype: "cancelled",
      });
    } else {
      emit({
        type: "result",
        subtype: "error_during_execution",
        errors: ["sendAndWait returned undefined (possible timeout)"],
      });
    }
  } else {
    emit({
      type: "result",
      subtype: "success",
      result: result.data.content,
    });
  }
  aborted = false;
}

function buildMcpServers(
  servers: Record<string, McpServerConfig>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, config] of Object.entries(servers)) {
    out[name] = {
      ...config,
      tools: config.tools ?? ["*"],
    };
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
  }
}

main().catch((e) => {
  process.stderr.write(`fatal: ${e}\n`);
  process.exit(1);
});
