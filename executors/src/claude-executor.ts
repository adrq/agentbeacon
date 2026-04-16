// Redirect console to stderr before any imports — SDK or transitive deps
// may call console.log which would corrupt the JSON Lines protocol on stdout.
console.log = (...args: unknown[]) => console.error(...args);
console.info = (...args: unknown[]) => console.error(...args);
console.warn = (...args: unknown[]) => console.error(...args);
console.debug = (...args: unknown[]) => console.error(...args);

import * as readline from "node:readline";
import type { Command, StartCommand, Part, Event } from "./common/protocol.js";

const { query, AbortError } = await import("@anthropic-ai/claude-agent-sdk");
import { emit } from "./common/stdio-bridge.js";

const MAX_TRANSIENT_RETRIES = 2;
const RETRY_DELAY_MS = 1000;

type TurnState = "idle" | "starting" | "in-flight";

function isTransientError(e: unknown): boolean {
  const msg = String(e);
  // Only retry network-level errors, not HTTP status errors (401/403/etc.)
  if (msg.includes("AxiosError")) {
    return (
      msg.includes("timeout") ||
      msg.includes("ECONN") ||
      msg.includes("ETIMEDOUT") ||
      msg.includes("ENOTFOUND") ||
      msg.includes("ENETUNREACH")
    );
  }
  // Standalone network errors (not wrapped in AxiosError)
  return (
    msg.includes("ECONN") ||
    msg.includes("ETIMEDOUT") ||
    msg.includes("ENOTFOUND") ||
    msg.includes("ENETUNREACH")
  );
}

// Orchestration tools that bypass AgentBeacon's coordination layer.
// Blocked via disallowedTools — reliable in ALL permission modes.
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
  "SendMessage", // Inter-agent messaging
  "SendMessageTool", // Alternate name for SendMessage (block both defensively)
];

// --- Command queue (single stdin listener, cancel as side-effect) ---

let currentAc: AbortController | null = null;
let stoppedByUser = false;
let pendingStopTurn = false;
let sessionGeneration = 0;
let turnState: TurnState = "idle";

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

function interruptPromptStream(): void {
  commandQueue.push({ type: "cancel" } as Command);
  if (queueResolve) {
    queueResolve();
    queueResolve = null;
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
  if (cmd.type === "cancel" && currentAc && turnState !== "idle") {
    currentAc.abort();
  } else if (cmd.type === "cancel") {
    // Cancel received while idle — shut down cleanly.
    shutdownRequested = true;
    discardQueuedTurnCommands();
    // Push a stop command to wake nextCommand() and break whatever loop is
    // currently awaiting it (main loop or promptStream generator).
    commandQueue.push({ type: "stop" } as Command);
    if (queueResolve) {
      queueResolve();
      queueResolve = null;
    }
  } else if (cmd.type === "stop_turn") {
    // Buffered locally until the worker can deliver it.
    if (currentAc && (turnState !== "idle" || hasQueuedTurnCommand())) {
      stoppedByUser = true;
      discardQueuedTurnCommands();
      interruptPromptStream();
      currentAc.abort();
    } else if (turnState === "starting" || hasQueuedTurnCommand()) {
      pendingStopTurn = true;
    } else {
      process.stderr.write(`[claude] ignoring stale stop_turn while idle\n`);
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

// --- Parts → Claude SDK content blocks ---

// SDK 0.2.104 narrowed Base64ImageSource.media_type to this literal union.
type ImageMediaType = "image/jpeg" | "image/png" | "image/gif" | "image/webp";
const SUPPORTED_IMAGE_MEDIA_TYPES: readonly ImageMediaType[] = [
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
];

type ContentBlock =
  | { type: "text"; text: string }
  | {
      type: "image";
      source: { type: "base64"; media_type: ImageMediaType; data: string };
    };

function partsToContent(parts: Part[]): ContentBlock[] {
  return parts.flatMap((part): ContentBlock[] => {
    if ("text" in part)
      return [{ type: "text", text: (part as { text: string }).text }];
    if ("raw" in part) {
      const raw = part as {
        raw: string;
        mediaType?: string;
        filename?: string;
      };
      const isSupportedImage =
        !!raw.raw &&
        !!raw.mediaType &&
        (SUPPORTED_IMAGE_MEDIA_TYPES as readonly string[]).includes(
          raw.mediaType,
        );
      if (isSupportedImage) {
        return [
          {
            type: "image",
            source: {
              type: "base64",
              media_type: raw.mediaType as ImageMediaType,
              data: raw.raw,
            },
          },
        ];
      }
      // Non-image (or unsupported image) file: represent as text so the model
      // knows an attachment exists.
      const name = raw.filename ?? "attachment";
      const mime = raw.mediaType ?? "application/octet-stream";
      return [{ type: "text", text: `[File: ${name} (${mime})]` }];
    }
    return [];
  });
}

// --- Async generator feeding prompts into query() ---

async function* promptStream(
  startCmd: StartCommand,
  gen: number,
): AsyncGenerator<{
  type: "user";
  session_id: string;
  message: { role: "user"; content: ContentBlock[] };
  parent_tool_use_id: null;
}> {
  turnState = "in-flight";
  yield {
    type: "user",
    session_id: "",
    message: { role: "user", content: partsToContent(startCmd.parts) },
    parent_tool_use_id: null,
  };

  while (true) {
    const cmd = await nextCommand();
    if (gen !== sessionGeneration) return;
    if (cmd.type === "stop" || cmd.type === "eof" || cmd.type === "cancel") {
      return;
    }
    if (cmd.type === "start") {
      emit({
        type: "error",
        message: "Received start command during active session",
      });
      return;
    }
    if (cmd.type === "prompt") {
      emit({ type: "accepted" });
      turnState = "in-flight";
      yield {
        type: "user",
        session_id: "",
        message: { role: "user", content: partsToContent(cmd.parts) },
        parent_tool_use_id: null,
      };
    }
  }
}

// --- Main loop ---

async function main(): Promise<void> {
  let resumableStart: StartCommand | null = null;

  function preserveResumableStart(
    startCmd: StartCommand,
    currentSessionId?: string,
  ): void {
    resumableStart = currentSessionId
      ? { ...startCmd, resumeSessionId: currentSessionId }
      : { ...startCmd };
  }

  function emitAbortedBeforeQuery(
    startCmd: StartCommand,
    currentSessionId?: string,
  ): void {
    emit({
      type: "result",
      subtype: stoppedByUser ? "stopped_by_user" : "cancelled",
      sessionId: currentSessionId,
    });
    if (stoppedByUser) {
      preserveResumableStart(startCmd, currentSessionId);
    }
    discardQueuedTurnCommands();
    pendingStopTurn = false;
    stoppedByUser = false;
  }

  while (true) {
    if (shutdownRequested) break;
    const cmd = await nextCommand();
    if (cmd.type === "stop" || cmd.type === "eof") break;

    let startCmd: StartCommand;
    if (cmd.type === "start") {
      resumableStart = null;
      startCmd = cmd;
    } else {
      const resumeConfig: StartCommand | null = resumableStart;
      if (cmd.type === "prompt" && resumeConfig !== null) {
        startCmd = Object.assign({}, resumeConfig, { parts: cmd.parts });
      } else {
        continue;
      }
    }

    turnState = "starting";

    sessionGeneration++;
    let currentSessionId: string | undefined;
    let lastError: unknown = null;

    // Buffered assistant message — held until message_stop confirms it's final.
    // Reset per attempt to prevent stale leakage across retries.
    let pendingAssistant: unknown[] | null = null;

    // Track the last complete assistant response to populate result.result
    // when the SDK omits it.
    let lastFinalOutput: string | null = null;

    try {
      for (let attempt = 0; attempt <= MAX_TRANSIENT_RETRIES; attempt++) {
        currentAc = new AbortController();
        lastError = null;
        pendingAssistant = null;
        lastFinalOutput = null;

        if (pendingStopTurn) {
          stoppedByUser = true;
          currentAc.abort();
        }

        if (attempt > 0) {
          await new Promise((r) => setTimeout(r, RETRY_DELAY_MS));
          // Don't start a new query() if stop/eof arrived during the delay
          if (commandQueue.some((c) => c.type === "stop" || c.type === "eof")) {
            lastError = null;
            break;
          }
          // Don't start a new query() if cancel aborted the controller during the delay
          if (currentAc.signal.aborted) {
            emitAbortedBeforeQuery(startCmd, currentSessionId);
            break;
          }
        }

        if (currentAc.signal.aborted) {
          emitAbortedBeforeQuery(startCmd, currentSessionId);
          break;
        }

        try {
          const options: Record<string, unknown> = {
            abortController: currentAc,
            cwd: startCmd.cwd,
            permissionMode: "bypassPermissions" as const,
            allowDangerouslySkipPermissions: true,
            settingSources: ["project"],
            stderr: (data: string) => process.stderr.write(data),
            disallowedTools: DISALLOWED_ORCHESTRATION_TOOLS,
          };

          if (startCmd.mcpServers) options.mcpServers = startCmd.mcpServers;
          if (startCmd.model) options.model = startCmd.model;
          if (startCmd.maxTurns != null) options.maxTurns = startCmd.maxTurns;
          if (startCmd.maxBudgetUsd != null) {
            options.maxBudgetUsd = startCmd.maxBudgetUsd;
          }
          if (startCmd.systemPrompt) {
            options.systemPrompt = {
              type: "preset",
              preset: "claude_code",
              append: startCmd.systemPrompt,
            };
          }
          if (startCmd.resumeSessionId)
            options.resume = startCmd.resumeSessionId;
          if (startCmd.thinking) options.thinking = startCmd.thinking;
          if (startCmd.effort) options.effort = startCmd.effort;
          options.includePartialMessages = true;

          const q = query({
            prompt: promptStream(startCmd, sessionGeneration),
            options,
          });

          for await (const msg of q) {
            if (msg.type === "system") {
              const subtype = "subtype" in msg ? msg.subtype : undefined;
              if (subtype === "init") {
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
              } else if (subtype === "compact_boundary") {
                const meta = (msg as Record<string, unknown>)
                  .compact_metadata as Record<string, unknown> | undefined;
                emit({
                  type: "message",
                  role: "assistant",
                  content: [
                    {
                      type: "compaction",
                      trigger:
                        typeof meta?.trigger === "string"
                          ? meta.trigger
                          : "auto",
                    },
                  ],
                });
              } else {
                // Any other system subtype (api_retry, status, hook_*,
                // task_*, local_command_output, files_persisted, ...) is
                // forwarded as-is.
                emit({
                  type: "message",
                  role: "assistant",
                  content: [msg as unknown as Record<string, unknown>],
                });
              }
            } else if (msg.type === "assistant") {
              const inner =
                "message" in msg &&
                msg.message &&
                typeof msg.message === "object"
                  ? (msg.message as unknown as Record<string, unknown>)
                  : undefined;

              // Emit assistant-level errors immediately. On auth/billing
              // failures the SDK can skip message_stop and go straight to
              // result, which discards pendingAssistant. Buffering would
              // therefore lose the error.
              const assistantError =
                inner && typeof inner.error === "string"
                  ? inner.error
                  : typeof (msg as Record<string, unknown>).error === "string"
                    ? ((msg as Record<string, unknown>).error as string)
                    : undefined;
              if (assistantError) {
                emit({
                  type: "message",
                  role: "assistant",
                  content: [{ type: "assistant_error", error: assistantError }],
                });
              }

              const rawContent = inner?.content;
              const content: unknown[] = Array.isArray(rawContent)
                ? [...rawContent]
                : [];

              // input_tokens excludes cached tokens — sum with cache_read_input_tokens
              // and cache_creation_input_tokens for true context consumption.
              const usage = inner?.usage as Record<string, unknown> | undefined;
              if (usage && typeof usage.input_tokens === "number") {
                const cacheRead =
                  typeof usage.cache_read_input_tokens === "number"
                    ? usage.cache_read_input_tokens
                    : 0;
                const cacheCreation =
                  typeof usage.cache_creation_input_tokens === "number"
                    ? usage.cache_creation_input_tokens
                    : 0;
                content.push({
                  type: "usage_update",
                  input_tokens: usage.input_tokens + cacheRead + cacheCreation,
                  output_tokens:
                    typeof usage.output_tokens === "number"
                      ? usage.output_tokens
                      : 0,
                });
              }

              // Accumulate content blocks — the SDK emits per-block
              // assistant messages (not cumulative snapshots), so we must
              // collect all blocks and flush together on message_stop.
              pendingAssistant = [...(pendingAssistant ?? []), ...content];
            } else if (msg.type === "user") {
              const content =
                "message" in msg &&
                msg.message &&
                typeof msg.message === "object"
                  ? (msg.message as unknown as Record<string, unknown>).content
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
              // Discard any unflushed assistant snapshot — if message_stop was
              // missed, the content is incomplete and should not be persisted.
              pendingAssistant = null;
              // Capture then clear the executor-owned fallback so the next
              // turn starts clean. Any stale value from an earlier turn must
              // not leak into this result.
              const fallbackResult = lastFinalOutput;
              lastFinalOutput = null;
              turnState = "idle";
              const m = msg as unknown as Record<string, unknown>;

              // Emit usage_snapshot as a standalone message — contextWindow comes from
              // the result event which doesn't flow through the message pipeline
              const modelUsage = (m.model_usage ?? m.modelUsage) as
                | Record<string, Record<string, unknown>>
                | undefined;
              let maxContextWindow = 0;
              if (modelUsage && typeof modelUsage === "object") {
                // Skip models without contextWindow field (e.g., old SDK versions).
                for (const modelInfo of Object.values(modelUsage)) {
                  if (
                    modelInfo &&
                    typeof modelInfo === "object" &&
                    typeof modelInfo.contextWindow === "number" &&
                    modelInfo.contextWindow > maxContextWindow
                  ) {
                    maxContextWindow = modelInfo.contextWindow;
                  }
                }
              }

              if (maxContextWindow > 0) {
                const snapshotBlock: Record<string, unknown> = {
                  type: "usage_snapshot",
                  context_window: maxContextWindow,
                };
                emit({
                  type: "message",
                  role: "assistant",
                  content: [snapshotBlock],
                });
              }

              const resultEvent: Record<string, unknown> = {
                type: "result",
                subtype: (m.subtype as string) ?? "success",
                sessionId: currentSessionId,
                costUsd:
                  typeof m.total_cost_usd === "number"
                    ? m.total_cost_usd
                    : undefined,
                numTurns:
                  typeof m.num_turns === "number" ? m.num_turns : undefined,
                durationMs:
                  typeof m.duration_ms === "number" ? m.duration_ms : undefined,
              };
              if (m.subtype === "success" && typeof m.result === "string") {
                resultEvent.result = m.result;
              } else if (m.subtype === "success" && fallbackResult) {
                // SDK omitted result.result (observed with late api_retry
                // events and some error paths that still report success).
                // The executor populates it from the most recent message_stop
                // flush so the worker never has to fall back.
                resultEvent.result = fallbackResult;
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
                if (
                  delta.type === "text_delta" &&
                  typeof delta.text === "string"
                ) {
                  emit({
                    type: "message",
                    role: "assistant",
                    content: [{ type: "text_delta", text: delta.text }],
                    ephemeral: true,
                  });
                } else if (
                  delta.type === "thinking_delta" &&
                  typeof delta.thinking === "string"
                ) {
                  emit({
                    type: "message",
                    role: "assistant",
                    content: [
                      { type: "thinking_delta", thinking: delta.thinking },
                    ],
                    ephemeral: true,
                  });
                } else if (delta.type) {
                  // Catch-all for the remaining content_block_delta
                  // subtypes (input_json_delta, citations_delta,
                  // signature_delta, compaction_delta, plus any future ones).
                  // All deltas are streaming fragments -> ephemeral.
                  emit({
                    type: "message",
                    role: "assistant",
                    content: [delta],
                    ephemeral: true,
                  });
                }
              } else if (event && event.type === "message_stop") {
                // message_stop confirms the buffered assistant is the final
                // snapshot for this API call. Flush it as a persisted message
                // and capture its text as fallback for result.result.
                if (pendingAssistant) {
                  const flushed = pendingAssistant;
                  emit({
                    type: "message",
                    role: "assistant",
                    content: flushed,
                  });
                  lastFinalOutput = flushed
                    .filter(
                      (b): b is { type: string; text: string } =>
                        typeof b === "object" &&
                        b !== null &&
                        (b as { type?: unknown }).type === "text" &&
                        typeof (b as { text?: unknown }).text === "string",
                    )
                    .map((b) => b.text)
                    .join("");
                  pendingAssistant = null;
                }
              } else if (event) {
                // Any other stream_event subtype (message_start,
                // content_block_start/stop, message_delta, future ones)
                // is a streaming lifecycle fragment -> ephemeral. Worth
                // transporting on SSE for observability without polluting
                // persisted history.
                emit({
                  type: "message",
                  role: "assistant",
                  content: [event],
                  ephemeral: true,
                });
              }
            } else {
              // Any unhandled top-level msg.type (auth_status,
              // tool_progress, tool_use_summary, rate_limit_event,
              // prompt_suggestion, future SDK additions). Raw passthrough.
              // tool_progress fires every few seconds per tool — mark
              // ephemeral; everything else is persisted by default.
              const top = msg as unknown as Record<string, unknown>;
              emit({
                type: "message",
                role: "assistant",
                content: [top],
                ephemeral: top.type === "tool_progress",
              });
            }
          }
          break; // success — exit retry loop
        } catch (e: unknown) {
          lastError = e;

          // Per-attempt cleanup: invalidate abandoned promptStream generator,
          // unblock it if stuck in nextCommand(), abort old controller.
          currentAc.abort();
          sessionGeneration++;
          commandQueue.push({ type: "cancel" } as Command);
          if (queueResolve) {
            queueResolve();
            queueResolve = null;
          }

          // Only retry transient errors on fresh (non-resume) sessions before init.
          if (
            isTransientError(e) &&
            !currentSessionId &&
            !startCmd.resumeSessionId &&
            attempt < MAX_TRANSIENT_RETRIES
          ) {
            process.stderr.write(
              `[claude] transient SDK error: ${String(e).slice(0, 200)}, retry ${attempt + 1}/${MAX_TRANSIENT_RETRIES}\n`,
            );
            // Bump generation again for the fresh attempt's promptStream
            sessionGeneration++;
            continue;
          }
          break; // non-retryable or exhausted
        }
      }

      // Handle final error (if any) — discard incomplete buffered content
      pendingAssistant = null;
      lastFinalOutput = null;
      if (lastError) {
        if (lastError instanceof AbortError) {
          turnState = "idle";
          emit({
            type: "result",
            subtype: stoppedByUser ? "stopped_by_user" : "cancelled",
            sessionId: currentSessionId,
          });
          if (stoppedByUser) {
            preserveResumableStart(startCmd, currentSessionId);
          } else {
            resumableStart = null;
          }
          stoppedByUser = false;
          pendingStopTurn = false;
        } else {
          resumableStart = null;
          emit({ type: "error", message: String(lastError) });
        }
      }
    } finally {
      turnState = "idle";
      currentAc = null;
      pendingStopTurn = false;
    }
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
