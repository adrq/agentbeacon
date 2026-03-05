// Redirect console to stderr before any imports — SDK or transitive deps
// may call console.log which would corrupt the JSON Lines protocol on stdout.
console.log = (...args: unknown[]) => console.error(...args);
console.info = (...args: unknown[]) => console.error(...args);
console.warn = (...args: unknown[]) => console.error(...args);
console.debug = (...args: unknown[]) => console.error(...args);

import * as readline from "node:readline";
import type { Command, StartCommand, Event } from "./common/protocol.js";

const { query } =
  process.env.AGENTBEACON_MOCK_SDK === "1"
    ? await import("./mock-claude-sdk.js")
    : await import("@anthropic-ai/claude-agent-sdk");
import { emit } from "./common/stdio-bridge.js";

// Orchestration tools that bypass AgentBeacon's coordination layer.
// Blocked via disallowedTools — reliable in ALL permission modes.
// See: kb/research/118-claude-sdk-orchestration-tools-deep-dive.md
const DISALLOWED_ORCHESTRATION_TOOLS: string[] = [
  "Agent", // Subagent spawning (renamed from Task in v2.1.63)
  "Task", // Legacy alias for Agent
  "TaskOutput", // Retrieve output from background tasks
  "TaskStop", // Stop background tasks
  "TeamCreate", // Multi-agent swarm infrastructure
  "TeamDelete", // Delete team infrastructure
  "TaskCreate", // Team task board operations
  "TaskUpdate",
  "TaskList",
  "TaskGet",
  "SendMessage", // Inter-agent messaging outside AgentBeacon
  "SendMessageTool", // Alternate name for SendMessage (block both defensively)
];

// --- Command queue (single stdin listener, cancel as side-effect) ---

let currentAc: AbortController | null = null;
let sessionGeneration = 0;

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
  if (cmd.type === "cancel" && currentAc) {
    currentAc.abort();
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

// --- Async generator feeding prompts into query() ---

async function* promptStream(
  startCmd: StartCommand,
  gen: number,
): AsyncGenerator<{
  type: "user";
  session_id: string;
  message: { role: "user"; content: string };
  parent_tool_use_id: null;
}> {
  yield {
    type: "user",
    session_id: "",
    message: { role: "user", content: startCmd.prompt },
    parent_tool_use_id: null,
  };

  while (true) {
    const cmd = await nextCommand();
    if (gen !== sessionGeneration) return;
    if (cmd.type === "stop" || cmd.type === "eof") return;
    if (cmd.type === "start") {
      emit({
        type: "error",
        message: "Received start command during active session",
      });
      return;
    }
    if (cmd.type === "prompt") {
      yield {
        type: "user",
        session_id: "",
        message: { role: "user", content: cmd.text },
        parent_tool_use_id: null,
      };
    }
  }
}

// --- Main loop ---

async function main(): Promise<void> {
  while (true) {
    const cmd = await nextCommand();
    if (cmd.type === "stop" || cmd.type === "eof") break;

    if (cmd.type !== "start") continue;

    sessionGeneration++;
    currentAc = new AbortController();
    let currentSessionId: string | undefined;

    try {
      const options: Record<string, unknown> = {
        abortController: currentAc,
        cwd: cmd.cwd,
        permissionMode: "bypassPermissions" as const,
        allowDangerouslySkipPermissions: true,
        settingSources: ["project"],
        stderr: (data: string) => process.stderr.write(data),
        disallowedTools: DISALLOWED_ORCHESTRATION_TOOLS,
      };

      if (cmd.mcpServers) options.mcpServers = cmd.mcpServers;
      if (cmd.model) options.model = cmd.model;
      if (cmd.maxTurns != null) options.maxTurns = cmd.maxTurns;
      if (cmd.maxBudgetUsd != null) options.maxBudgetUsd = cmd.maxBudgetUsd;
      if (cmd.systemPrompt) {
        options.systemPrompt = {
          type: "preset",
          preset: "claude_code",
          append: cmd.systemPrompt,
        };
      }
      if (cmd.resumeSessionId) options.resume = cmd.resumeSessionId;
      options.includePartialMessages = true;

      const q = query({
        prompt: promptStream(cmd, sessionGeneration),
        options,
      });

      for await (const msg of q) {
        if (
          msg.type === "system" &&
          "subtype" in msg &&
          msg.subtype === "init"
        ) {
          currentSessionId = msg.session_id;
          const mcpServers =
            "mcp_servers" in msg && Array.isArray(msg.mcp_servers)
              ? msg.mcp_servers
              : undefined;
          emit({
            type: "init",
            sessionId: msg.session_id,
            mcpServers,
          });
        } else if (msg.type === "assistant") {
          const content =
            "message" in msg && msg.message && typeof msg.message === "object"
              ? (msg.message as Record<string, unknown>).content
              : undefined;
          emit({
            type: "message",
            role: "assistant",
            content: Array.isArray(content) ? content : [],
          });
        } else if (msg.type === "user") {
          const content =
            "message" in msg && msg.message && typeof msg.message === "object"
              ? (msg.message as Record<string, unknown>).content
              : undefined;
          if (Array.isArray(content)) {
            const toolResults = content.filter(
              (b: Record<string, unknown>) => b?.type === "tool_result",
            );
            if (toolResults.length > 0) {
              emit({
                type: "message",
                role: "assistant",
                content: toolResults,
              });
            }
          }
        } else if (msg.type === "result") {
          const m = msg as unknown as Record<string, unknown>;
          const resultEvent: Record<string, unknown> = {
            type: "result",
            subtype: (m.subtype as string) ?? "success",
            sessionId: currentSessionId,
            costUsd:
              typeof m.total_cost_usd === "number"
                ? m.total_cost_usd
                : undefined,
            numTurns: typeof m.num_turns === "number" ? m.num_turns : undefined,
            durationMs:
              typeof m.duration_ms === "number" ? m.duration_ms : undefined,
          };
          if (m.subtype === "success" && typeof m.result === "string") {
            resultEvent.result = m.result;
          }
          if (Array.isArray(m.errors)) {
            resultEvent.errors = m.errors;
          }
          emit(resultEvent as unknown as Event);
        } else if (msg.type === "stream_event") {
          const m = msg as unknown as Record<string, unknown>;
          const event = m.event as Record<string, unknown> | undefined;
          if (
            event &&
            event.type === "content_block_delta" &&
            typeof event.delta === "object" &&
            event.delta !== null
          ) {
            const delta = event.delta as Record<string, unknown>;
            if (delta.type === "text_delta" && typeof delta.text === "string") {
              emit({
                type: "message",
                role: "assistant",
                content: [{ type: "text_delta", text: delta.text }],
              });
            }
          } else if (event) {
            process.stderr.write(
              `[claude] ignoring non-text stream_event: ${JSON.stringify(event.type ?? "unknown")}\n`,
            );
          }
        }
        // Silently skip: user replay, compact_boundary
      }
    } catch (e: unknown) {
      sessionGeneration++;
      // Non-terminal sentinel to unblock promptStream if stuck in nextCommand().
      // Using "cancel" (not "eof") so it's harmlessly skipped if it leaks to the main loop.
      commandQueue.push({ type: "cancel" } as Command);
      if (queueResolve) {
        queueResolve();
        queueResolve = null;
      }

      if (e instanceof Error && e.name === "AbortError") {
        emit({
          type: "result",
          subtype: "cancelled",
          sessionId: currentSessionId,
        });
      } else {
        emit({ type: "error", message: String(e) });
      }
    } finally {
      currentAc = null;
    }
  }
}

main().catch((e) => {
  process.stderr.write(`fatal: ${e}\n`);
  process.exit(1);
});
