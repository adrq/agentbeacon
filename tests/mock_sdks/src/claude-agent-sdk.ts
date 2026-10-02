// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import * as fs from "node:fs";

interface UserMessage {
  type: "user";
  session_id: string;
  message: { role: "user"; content: string | ContentBlock[] };
  parent_tool_use_id: string | null;
}

type ContentBlock = { type: string; [key: string]: unknown };

type MockSDKMessage =
  | {
      type: "system";
      subtype: "init";
      session_id: string;
      mcp_servers: { name: string; status: string }[];
    }
  | {
      type: "assistant";
      message: {
        content: ContentBlock[];
        stop_reason?: string | null;
        usage?: {
          input_tokens: number;
          output_tokens: number;
          cache_read_input_tokens?: number;
          cache_creation_input_tokens?: number;
        };
      };
      error?: string;
    }
  | {
      type: "user";
      message: { content: ContentBlock[] };
      session_id: string;
      parent_tool_use_id: string | null;
    }
  | {
      type: "result";
      subtype: string;
      result?: string;
      errors?: string[];
      total_cost_usd?: number;
      num_turns?: number;
      duration_ms?: number;
      model_usage?: Record<string, { contextWindow: number }>;
      usage?: {
        input_tokens: number;
        output_tokens: number;
        cache_read_input_tokens?: number;
        cache_creation_input_tokens?: number;
      };
    }
  | {
      type: "stream_event";
      event:
        | {
            type: "content_block_delta";
            index: number;
            delta: {
              type: string;
              text?: string;
              thinking?: string;
              partial_json?: string;
            };
          }
        | {
            type: "content_block_start";
            index: number;
            content_block: { type: string; [key: string]: unknown };
          }
        | { type: "content_block_stop"; index: number }
        | {
            type: "message_start";
            message: { id: string; type: "message"; role: "assistant" };
          }
        | {
            type: "message_delta";
            delta: { stop_reason: string | null };
            usage?: { output_tokens: number };
          }
        | { type: "message_stop" };
    }
  | {
      type: "system";
      subtype: "compact_boundary";
      compact_metadata?: { trigger: string; pre_tokens?: number };
    }
  | {
      type: "auth_status";
      isAuthenticating: boolean;
      output: string[];
      error?: string;
    }
  | {
      type: "system";
      subtype: "api_retry";
      attempt: number;
      max_retries: number;
      retry_delay_ms: number;
      error_status: number;
      error: string;
    }
  | {
      type: "tool_progress";
      tool_use_id: string;
      tool_name: string;
      progress: string;
    }
  | {
      type: "system";
      subtype: "model_refusal_fallback";
      trigger: string;
      direction: string;
      original_model: string;
      fallback_model: string;
      request_id: string | null;
      api_refusal_category?: string;
      api_refusal_explanation?: string;
      retracted_message_uuids?: string[];
      refused_user_message_uuid?: string | null;
      content: string;
      uuid: string;
      session_id: string;
    }
  | {
      type: "system";
      subtype: "model_refusal_no_fallback";
      original_model: string;
      request_id: string | null;
      api_refusal_category?: string;
      api_refusal_explanation?: string;
      refused_user_message_uuid?: string | null;
      content: string;
      uuid: string;
      session_id: string;
    }
  | {
      type: "conversation_reset";
      new_conversation_id: string;
      uuid: string;
      session_id: string;
    }
  | {
      type: "active_goal";
      value: {
        condition: string;
        iterations: number;
        set_at: number;
        tokens_at_start: number;
        last_reason?: string;
      } | null;
      uuid: string;
      session_id: string;
    };

let queryCallCount = 0;
const transientFailureCount = parseInt(
  process.env.AGENTBEACON_MOCK_SDK_TRANSIENT_FAILURES ?? "0",
  10,
);
const initDelayMs = parseInt(
  process.env.AGENTBEACON_MOCK_CLAUDE_INIT_DELAY_MS ?? "0",
  10,
);
const midStreamFailure =
  process.env.AGENTBEACON_MOCK_SDK_MID_STREAM_FAILURE === "1";
const authFailure = process.env.AGENTBEACON_MOCK_SDK_AUTH_FAILURE === "1";
const systemSubtypeTest =
  process.env.AGENTBEACON_MOCK_SDK_SYSTEM_SUBTYPE === "1";
const deltaSubtypeTest = process.env.AGENTBEACON_MOCK_SDK_DELTA_SUBTYPE === "1";
const toolProgressTest = process.env.AGENTBEACON_MOCK_SDK_TOOL_PROGRESS === "1";
const nonStreamedTurnTest =
  process.env.AGENTBEACON_MOCK_SDK_NONSTREAMED_TURN === "1";
const toolRaceTest = process.env.AGENTBEACON_MOCK_SDK_TOOL_RACE === "1";
const userNoFlushTest =
  process.env.AGENTBEACON_MOCK_SDK_USER_NOFLUSH === "1";
const refusalFallbackTest =
  process.env.AGENTBEACON_MOCK_SDK_REFUSAL_FALLBACK === "1";
const REFUSAL_FALLBACK_SENTINEL = "__MOCK_REFUSAL_FALLBACK__";
const REFUSAL_NO_FALLBACK_SENTINEL = "__MOCK_REFUSAL_NO_FALLBACK__";
const PROVISIONAL_SENTINEL = "__MOCK_PROVISIONAL__";
export const PROVISIONAL_DELTA_TOKEN = "PROVISIONAL-ONLY-TOKEN";
export const PROVISIONAL_PERSISTED_TOKEN = "PERSISTED-ONLY-TOKEN";
const autoResumeTest = process.env.AGENTBEACON_MOCK_SDK_AUTORESUME === "1";
const autoResumeHang =
  process.env.AGENTBEACON_MOCK_SDK_AUTORESUME_HANG === "1";
const autoResumeGateFile =
  process.env.AGENTBEACON_MOCK_SDK_AUTORESUME_GATE_FILE ?? "";

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export class AbortError extends Error {}

function abortError(): AbortError {
  return new AbortError("Aborted");
}

function checkAbort(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw abortError();
}

async function* abortAwareIterable<T>(
  iterable: AsyncIterable<T>,
  signal: AbortSignal | undefined,
): AsyncGenerator<T> {
  if (!signal) {
    yield* iterable;
    return;
  }

  const iterator = iterable[Symbol.asyncIterator]();
  const abortPromise = new Promise<never>((_, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    signal.addEventListener("abort", () => reject(abortError()), {
      once: true,
    });
  });
  try {
    while (true) {
      const result = await Promise.race([iterator.next(), abortPromise]);
      if (result.done) break;
      yield result.value;
    }
  } finally {
    await iterator.return?.();
  }
}

let mockMsgSeq = 0;
function msgId(): string {
  return `msg_mock_${++mockMsgSeq}`;
}

async function* showcaseTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] showcaseTurn starting\n`);

  const id1 = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: id1, type: "message", role: "assistant" },
    },
  };

  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 0,
      content_block: { type: "thinking" },
    },
  };
  checkAbort(signal);
  await delay(30);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: { type: "thinking_delta", thinking: "Let me analyze" },
    },
  };
  checkAbort(signal);
  await delay(30);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: { type: "thinking_delta", thinking: " the codebase..." },
    },
  };

  checkAbort(signal);
  await delay(40);
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "thinking",
          thinking:
            "Let me analyze the codebase and figure out the best approach for this task...",
        },
      ],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "content_block_stop", index: 0 },
  };

  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 1,
      content_block: { type: "text", text: "" },
    },
  };
  checkAbort(signal);
  await delay(60);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 1,
      delta: {
        type: "text_delta",
        text: "I'll start by reading the configuration file.",
      },
    },
  };
  yield {
    type: "stream_event",
    event: { type: "content_block_stop", index: 1 },
  };
  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 2,
      content_block: { type: "tool_use", id: "toolu_mock_001", name: "Read" },
    },
  };
  yield {
    type: "stream_event",
    event: { type: "content_block_stop", index: 2 },
  };

  checkAbort(signal);
  await delay(30);
  yield {
    type: "assistant",
    message: {
      content: [
        { type: "text", text: "I'll start by reading the configuration file." },
      ],
    },
  };
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_001",
          name: "Read",
          input: { file_path: "/workspace/src/config.rs" },
        },
      ],
      usage: {
        input_tokens: 500,
        output_tokens: 350,
        cache_read_input_tokens: 12000,
        cache_creation_input_tokens: 0,
      },
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "tool_use" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  await delay(100);
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: {
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_001",
          content:
            "pub struct Config {\n    pub port: u16,\n    pub workers: usize,\n}",
          is_error: false,
        },
      ],
    },
  };

  const id2 = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: id2, type: "message", role: "assistant" },
    },
  };
  checkAbort(signal);
  await delay(120);
  yield {
    type: "assistant",
    message: {
      content: [
        { type: "text", text: "Now searching for TODO/FIXME items..." },
      ],
    },
  };
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_002",
          name: "Grep",
          input: { pattern: "TODO|FIXME", path: "/workspace/src" },
        },
      ],
      usage: {
        input_tokens: 1000,
        output_tokens: 420,
        cache_read_input_tokens: 12000,
        cache_creation_input_tokens: 12000,
      },
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "tool_use" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  await delay(50);
  yield {
    type: "system",
    subtype: "compact_boundary",
    compact_metadata: { trigger: "auto" },
  };

  checkAbort(signal);
  await delay(100);
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: {
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_002",
          content:
            "/workspace/src/main.rs:42: // TODO: add validation\n/workspace/src/config.rs:15: // FIXME: default port",
          is_error: false,
        },
      ],
    },
  };

  const id3 = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: id3, type: "message", role: "assistant" },
    },
  };
  checkAbort(signal);
  await delay(80);
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "thinking",
          thinking:
            "Found 2 issues to fix. I'll update config.rs with validation and fix the default port.",
        },
      ],
    },
  };
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_003",
          name: "Edit",
          input: {
            file_path: "/workspace/src/config.rs",
            old_string: "pub port: u16,",
            new_string: "pub port: u16, // default: 8080",
          },
        },
      ],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "tool_use" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  await delay(80);
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: {
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_003",
          content: "Successfully edited config.rs",
          is_error: false,
        },
      ],
    },
  };

  const id4 = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: id4, type: "message", role: "assistant" },
    },
  };
  checkAbort(signal);
  await delay(80);
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_todo_001",
          name: "TodoWrite",
          input: {
            todos: [
              {
                content: "Read configuration file",
                status: "completed",
                activeForm: "config.rs",
              },
              {
                content: "Search for TODO/FIXME items",
                status: "completed",
                activeForm: "src/",
              },
              {
                content: "Fix default port value",
                status: "completed",
                activeForm: "config.rs",
              },
              {
                content: "Add port validation",
                status: "in_progress",
                activeForm: "config.rs",
              },
              {
                content: "Update tests",
                status: "pending",
                activeForm: "tests/",
              },
            ],
          },
        },
      ],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "tool_use" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  await delay(50);
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: {
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_todo_001",
          content: JSON.stringify({
            oldTodos: [],
            newTodos: [
              {
                content: "Read configuration file",
                status: "completed",
                activeForm: "config.rs",
              },
              {
                content: "Search for TODO/FIXME items",
                status: "completed",
                activeForm: "src/",
              },
              {
                content: "Fix default port value",
                status: "completed",
                activeForm: "config.rs",
              },
              {
                content: "Add port validation",
                status: "in_progress",
                activeForm: "config.rs",
              },
              {
                content: "Update tests",
                status: "pending",
                activeForm: "tests/",
              },
            ],
          }),
          is_error: false,
        },
      ],
    },
  };

  const id5 = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: id5, type: "message", role: "assistant" },
    },
  };

  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 0,
      content_block: { type: "text", text: "" },
    },
  };
  checkAbort(signal);
  await delay(30);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: "# Changes" },
    },
  };
  checkAbort(signal);
  await delay(30);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: " Complete\n\nFixed" },
    },
  };

  yield {
    type: "stream_event",
    event: { type: "content_block_stop", index: 0 },
  };

  checkAbort(signal);
  await delay(100);
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "text",
          text: [
            "# Changes Complete",
            "",
            "Fixed both issues:",
            "- Added port validation (must be > 0)",
            "- Set default port to 8080",
            "",
            "| File | Changes | Status |",
            "|------|---------|--------|",
            "| `src/config.rs` | +12 -3 | Modified |",
            "| `src/main.rs` | +1 -1 | Modified |",
            "",
            "```rust",
            "pub fn validate(&self) -> Result<(), ConfigError> {",
            "    if self.port == 0 {",
            "        return Err(ConfigError::InvalidPort);",
            "    }",
            "    Ok(())",
            "}",
            "```",
          ].join("\n"),
        },
      ],
      usage: {
        input_tokens: 2000,
        output_tokens: 850,
        cache_read_input_tokens: 24000,
        cache_creation_input_tokens: 12000,
      },
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "end_turn" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  yield {
    type: "result",
    subtype: "success",
    result: "Fixed 2 TODO/FIXME items.",
    total_cost_usd: 0.042,
    num_turns: 1,
    duration_ms: 4500,
    model_usage: { "claude-sonnet-4-5-20250929": { contextWindow: 200000 } },
    usage: {
      input_tokens: 3500,
      output_tokens: 1620,
      cache_read_input_tokens: 48000,
      cache_creation_input_tokens: 24000,
    },
  };
}

async function* midStreamFailureTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] midStreamFailureTurn starting\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 0,
      content_block: { type: "text", text: "" },
    },
  };
  checkAbort(signal);
  await delay(30);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: "Stale content that should be discarded" },
    },
  };
  yield {
    type: "assistant",
    message: {
      content: [
        { type: "text", text: "Stale content that should be discarded" },
      ],
    },
  };
  throw new Error("Simulated mid-stream SDK failure");
}

async function* authFailureTurn(
  _sessionId: string,
  _signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] authFailureTurn starting\n`);
  yield {
    type: "auth_status",
    isAuthenticating: false,
    output: ["auth_token=REDACTED_TOKEN_VALUE", "Authentication failed"],
    error: "Invalid API key",
  };
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  yield {
    type: "assistant",
    message: {
      content: [{ type: "text", text: "Authentication error occurred." }],
    },
    error: "authentication_failed",
  };
  yield {
    type: "result",
    subtype: "error_during_execution",
    errors: ["authentication_failed"],
    total_cost_usd: 0,
    num_turns: 0,
    duration_ms: 100,
  };
}

async function* systemSubtypeTurn(
  _sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] systemSubtypeTurn starting\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  checkAbort(signal);
  await delay(20);
  yield {
    type: "assistant",
    message: {
      content: [{ type: "text", text: "Retried successfully." }],
    },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "system",
    subtype: "api_retry",
    attempt: 1,
    max_retries: 3,
    retry_delay_ms: 1000,
    error_status: 429,
    error: "rate_limit",
  };
  yield {
    type: "result",
    subtype: "success",
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 500,
  };
}

async function* refusalFallbackTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] refusalFallbackTurn starting\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  checkAbort(signal);
  await delay(20);
  yield {
    type: "assistant",
    message: {
      content: [{ type: "text", text: "Answer from the fallback model." }],
    },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "system",
    subtype: "model_refusal_fallback",
    trigger: "refusal",
    direction: "retry",
    original_model: "claude-sonnet-5",
    fallback_model: "claude-haiku-4-5",
    request_id: "req-mock-fallback-1",
    api_refusal_category: "cyber",
    api_refusal_explanation: "The request was declined for policy reasons.",
    retracted_message_uuids: ["uuid-refused-1", "uuid-refused-2"],
    refused_user_message_uuid: "uuid-user-1",
    content: "Refusal reason: the prompt requested disallowed content.",
    uuid: "uuid-fallback-1",
    session_id: sessionId,
  };
  yield {
    type: "conversation_reset",
    new_conversation_id: "conv-mock-reset-1",
    uuid: "uuid-reset-1",
    session_id: sessionId,
  };
  yield {
    type: "active_goal",
    value: {
      condition: "mock goal active",
      iterations: 1,
      set_at: 1700000000000,
      tokens_at_start: 1234,
    },
    uuid: "uuid-goal-1",
    session_id: sessionId,
  };
  yield {
    type: "result",
    subtype: "success",
    result: "Answer from the fallback model.",
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 500,
  };
}

async function* refusalNoFallbackTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] refusalNoFallbackTurn starting\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  checkAbort(signal);
  await delay(20);
  yield {
    type: "assistant",
    message: {
      content: [{ type: "text", text: "Unable to complete this request." }],
    },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "system",
    subtype: "model_refusal_no_fallback",
    original_model: "claude-sonnet-5",
    request_id: "req-mock-nofallback-1",
    api_refusal_category: "bio",
    api_refusal_explanation: "The request was declined; no fallback available.",
    refused_user_message_uuid: "uuid-user-2",
    content: "Content refusal with no fallback available.",
    uuid: "uuid-nofallback-1",
    session_id: sessionId,
  };
  yield {
    type: "result",
    subtype: "success",
    result: "Unable to complete this request.",
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 500,
  };
}

async function* deltaSubtypeTurn(
  _sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] deltaSubtypeTurn starting\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 0,
      content_block: { type: "tool_use", id: "toolu_mock_delta", name: "Read" },
    },
  };
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: {
        type: "input_json_delta",
        partial_json: '{"file_path": "/tmp/test',
      },
    },
  };
  checkAbort(signal);
  await delay(20);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: { type: "input_json_delta", partial_json: '.txt"}' },
    },
  };
  yield {
    type: "stream_event",
    event: { type: "content_block_stop", index: 0 },
  };
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_delta",
          name: "Read",
          input: { file_path: "/tmp/test.txt" },
        },
      ],
    },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "result",
    subtype: "success",
    result: "Delta test done.",
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 200,
  };
}

async function* provisionalDeltaTurn(
  _sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] provisionalDeltaTurn starting\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 0,
      content_block: { type: "text" },
    },
  };

  for (let i = 0; i < 8; i++) {
    checkAbort(signal);
    await delay(900);
    yield {
      type: "stream_event",
      event: {
        type: "content_block_delta",
        index: 0,
        delta: { type: "text_delta", text: `${PROVISIONAL_DELTA_TOKEN} ` },
      },
    };
  }
  yield {
    type: "stream_event",
    event: { type: "content_block_stop", index: 0 },
  };

  checkAbort(signal);
  yield {
    type: "assistant",
    message: {
      content: [{ type: "text", text: PROVISIONAL_PERSISTED_TOKEN }],
    },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "result",
    subtype: "success",
    result: PROVISIONAL_PERSISTED_TOKEN,
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 8000,
  };
}

async function* toolProgressTurn(
  _sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] toolProgressTurn starting\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  yield {
    type: "assistant",
    message: {
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_tp",
          name: "Bash",
          input: { command: "long-running-cmd" },
        },
      ],
    },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "tool_progress",
    tool_use_id: "toolu_mock_tp",
    tool_name: "Bash",
    progress: "running...",
  };
  checkAbort(signal);
  await delay(20);
  yield {
    type: "tool_progress",
    tool_use_id: "toolu_mock_tp",
    tool_name: "Bash",
    progress: "still running...",
  };
  yield {
    type: "result",
    subtype: "success",
    result: "Tool done.",
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 200,
  };
}

async function* nonStreamedTurn(
  _sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] nonStreamedTurn starting\n`);
  checkAbort(signal);
  yield {
    type: "assistant",
    message: {
      stop_reason: "end_turn",
      content: [
        {
          type: "text",
          text: [
            "Summary of changes.",
            "",
            "```",
            "example -> output",
            "```",
            "",
            "Done.",
          ].join("\n"),
        },
      ],
      usage: {
        input_tokens: 1200,
        output_tokens: 64,
        cache_read_input_tokens: 0,
        cache_creation_input_tokens: 0,
      },
    },
  };
  yield {
    type: "result",
    subtype: "success",
    total_cost_usd: 0.002,
    num_turns: 1,
    duration_ms: 300,
  };
}

async function* racingToolTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] racingToolTurn starting\n`);

  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: msgId(), type: "message", role: "assistant" },
    },
  };
  yield {
    type: "assistant",
    message: {
      stop_reason: null,
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_race_001",
          name: "Bash",
          input: { command: "echo hi" },
        },
      ],
      usage: {
        input_tokens: 800,
        output_tokens: 40,
        cache_read_input_tokens: 0,
        cache_creation_input_tokens: 0,
      },
    },
  };
  checkAbort(signal);
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: {
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_race_001",
          content: "hi",
          is_error: false,
        },
      ],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "tool_use" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: msgId(), type: "message", role: "assistant" },
    },
  };
  yield {
    type: "assistant",
    message: {
      stop_reason: null,
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_race_err_001",
          name: "Bash",
          input: { command: "false" },
        },
      ],
      usage: {
        input_tokens: 820,
        output_tokens: 40,
        cache_read_input_tokens: 0,
        cache_creation_input_tokens: 0,
      },
    },
  };
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: {
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_race_err_001",
          content: "boom",
          is_error: true,
        },
      ],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "tool_use" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: msgId(), type: "message", role: "assistant" },
    },
  };
  yield {
    type: "assistant",
    message: {
      stop_reason: null,
      content: [
        {
          type: "tool_use",
          id: "toolu_mock_race_par_a",
          name: "Bash",
          input: { command: "echo a" },
        },
        {
          type: "tool_use",
          id: "toolu_mock_race_par_b",
          name: "Bash",
          input: { command: "echo b" },
        },
      ],
      usage: {
        input_tokens: 900,
        output_tokens: 60,
        cache_read_input_tokens: 0,
        cache_creation_input_tokens: 0,
      },
    },
  };
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: {
      content: [
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_race_par_a",
          content: "a",
          is_error: false,
        },
        {
          type: "tool_result",
          tool_use_id: "toolu_mock_race_par_b",
          content: "b",
          is_error: false,
        },
      ],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "tool_use" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: msgId(), type: "message", role: "assistant" },
    },
  };
  yield {
    type: "assistant",
    message: {
      stop_reason: null,
      content: [{ type: "text", text: "Both commands attempted." }],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "end_turn" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "result",
    subtype: "success",
    total_cost_usd: 0.003,
    num_turns: 1,
    duration_ms: 400,
  };
}

async function* userNoFlushTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] userNoFlushTurn starting\n`);
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id: msgId(), type: "message", role: "assistant" },
    },
  };
  yield {
    type: "assistant",
    message: {
      stop_reason: null,
      content: [{ type: "text", text: "BUFFERED_GUARD" }],
    },
  };
  checkAbort(signal);
  yield {
    type: "user",
    session_id: sessionId,
    parent_tool_use_id: null,
    message: { content: [{ type: "text", text: "noop" }] },
  };
  yield {
    type: "system",
    subtype: "compact_boundary",
    compact_metadata: { trigger: "manual" },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "end_turn" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };
  yield {
    type: "result",
    subtype: "success",
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 150,
  };
}

async function waitForGateFile(
  gateFile: string,
  signal: AbortSignal | undefined,
): Promise<void> {
  if (!gateFile) return;
  const deadline = Date.now() + 60000;
  while (!fs.existsSync(gateFile)) {
    checkAbort(signal);
    if (Date.now() > deadline) {
      process.stderr.write(
        `[mock-claude-sdk] autoResume gate timeout: ${gateFile}\n`,
      );
      return;
    }
    await delay(50);
  }
}

async function* autoResumeTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
  gateFile: string,
  hang: boolean,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] autoResumeTurn starting\n`);

  yield {
    type: "assistant",
    message: {
      stop_reason: "end_turn",
      content: [{ type: "text", text: "initial turn output" }],
    },
  };
  yield {
    type: "result",
    subtype: "success",
    result: "initial turn done",
    total_cost_usd: 0.001,
    num_turns: 1,
    duration_ms: 100,
  };

  checkAbort(signal);
  yield {
    type: "system",
    subtype: "init",
    session_id: sessionId,
    mcp_servers: [],
  };

  if (hang) {
    await new Promise<never>((_, reject) => {
      if (signal?.aborted) {
        reject(abortError());
        return;
      }
      signal?.addEventListener("abort", () => reject(abortError()), {
        once: true,
      });
    });
    return;
  }

  await waitForGateFile(gateFile, signal);

  checkAbort(signal);
  yield {
    type: "assistant",
    message: {
      stop_reason: "end_turn",
      content: [{ type: "text", text: "resumed turn output" }],
    },
  };
  yield {
    type: "result",
    subtype: "success",
    result: "resumed turn done",
    total_cost_usd: 0.001,
    num_turns: 2,
    duration_ms: 100,
  };
}

async function* echoTurn(
  prompt: string,
  sessionId: string,
  turnIndex: number,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] echoTurn #${turnIndex}\n`);
  const id = msgId();
  yield {
    type: "stream_event",
    event: {
      type: "message_start",
      message: { id, type: "message", role: "assistant" },
    },
  };
  yield {
    type: "stream_event",
    event: {
      type: "content_block_start",
      index: 0,
      content_block: { type: "text", text: "" },
    },
  };
  checkAbort(signal);
  await delay(50);
  yield {
    type: "stream_event",
    event: {
      type: "content_block_delta",
      index: 0,
      delta: { type: "text_delta", text: `Acknowledged: ${prompt}` },
    },
  };
  yield {
    type: "stream_event",
    event: { type: "content_block_stop", index: 0 },
  };
  yield {
    type: "assistant",
    message: {
      content: [{ type: "text", text: `Acknowledged: ${prompt}` }],
    },
  };
  yield {
    type: "stream_event",
    event: { type: "message_delta", delta: { stop_reason: "end_turn" } },
  };
  yield { type: "stream_event", event: { type: "message_stop" } };

  checkAbort(signal);
  yield {
    type: "result",
    subtype: "success",
    result: `Follow-up handled.`,
    total_cost_usd: 0.001,
    num_turns: turnIndex + 1,
    duration_ms: 200,
  };
}

export async function* query(params: {
  prompt: string | AsyncIterable<UserMessage>;
  options?: Record<string, unknown>;
}): AsyncGenerator<MockSDKMessage, void> {
  queryCallCount++;
  if (transientFailureCount > 0 && queryCallCount <= transientFailureCount) {
    const err = new Error("timeout of 5000ms exceeded");
    err.name = "AxiosError";
    throw err;
  }

  const signal = (
    params.options?.abortController as AbortController | undefined
  )?.signal;
  const sessionId = `mock-session-${Date.now()}`;
  process.stderr.write(
    `[mock-claude-sdk] query() called, sessionId=${sessionId}\n`,
  );

  const resumeId = params.options?.resume as string | undefined;
  if (resumeId) {
    process.stderr.write(
      `[mock-claude-sdk] resume=true, resumeSessionId=${resumeId}\n`,
    );
  } else {
    process.stderr.write(`[mock-claude-sdk] resume=false\n`);
  }
  const systemPromptOption = params.options?.systemPrompt as
    | { type: string; append?: string }
    | undefined;
  if (systemPromptOption?.append) {
    process.stderr.write(
      `[mock-claude-sdk] system_prompt_present=true, system_prompt_len=${systemPromptOption.append.length}\n`,
    );
    const hasAgentBeaconBriefing = systemPromptOption.append.includes(
      "# AgentBeacon Environment",
    );
    process.stderr.write(
      `[mock-claude-sdk] system_prompt_has_briefing=${hasAgentBeaconBriefing}\n`,
    );
  } else {
    process.stderr.write(`[mock-claude-sdk] system_prompt_present=false\n`);
  }

  if (initDelayMs > 0) {
    checkAbort(signal);
    await delay(initDelayMs);
    checkAbort(signal);
  }

  const disallowedTools = params.options?.disallowedTools;
  if (Array.isArray(disallowedTools) && disallowedTools.length > 0) {
    process.stderr.write(
      `[mock-claude-sdk] disallowedTools=${JSON.stringify(disallowedTools)}\n`,
    );
  } else {
    process.stderr.write(
      `[mock-claude-sdk] WARNING: no disallowedTools configured\n`,
    );
  }

  const optEnv = params.options?.env as Record<string, string> | undefined;
  process.stderr.write(
    `[mock-claude-sdk] settings=${JSON.stringify(params.options?.settings)}\n`,
  );
  process.stderr.write(
    `[mock-claude-sdk] thinking=${JSON.stringify(params.options?.thinking)}\n`,
  );
  process.stderr.write(
    `[mock-claude-sdk] disableAutoMemoryEnv=${optEnv?.CLAUDE_CODE_DISABLE_AUTO_MEMORY}\n`,
  );
  process.stderr.write(
    `[mock-claude-sdk] envHasPath=${Boolean(optEnv?.PATH)}\n`,
  );

  const sandbox = params.options?.sandbox;
  const permMode = params.options?.permissionMode;
  const dangerousSkip = params.options?.allowDangerouslySkipPermissions;
  process.stderr.write(
    `[mock-claude-sdk] permissionMode=${String(permMode)}\n`,
  );
  process.stderr.write(
    `[mock-claude-sdk] allowDangerouslySkipPermissions=${String(dangerousSkip)}\n`,
  );
  if (sandbox != null) {
    process.stderr.write(
      `[mock-claude-sdk] sandbox=${JSON.stringify(sandbox)}\n`,
    );
  } else {
    process.stderr.write(`[mock-claude-sdk] sandbox=none\n`);
  }

  yield {
    type: "system",
    subtype: "init",
    session_id: sessionId,
    mcp_servers: [],
  };

  if (typeof params.prompt === "string") {
    yield* showcaseTurn(sessionId, signal);
  } else {
    let turnIndex = 0;
    for await (const userMsg of abortAwareIterable(params.prompt, signal)) {
      const content = userMsg.message.content;
      const promptText =
        typeof content === "string"
          ? content
          : (content as ContentBlock[])
              .filter((b) => b.type === "text")
              .map((b) => b.text as string)
              .join(" ");

      if (turnIndex === 0 && midStreamFailure) {
        yield* midStreamFailureTurn(sessionId, signal);
      } else if (turnIndex === 0 && authFailure) {
        yield* authFailureTurn(sessionId, signal);
      } else if (turnIndex === 0 && systemSubtypeTest) {
        yield* systemSubtypeTurn(sessionId, signal);
      } else if (turnIndex === 0 && deltaSubtypeTest) {
        yield* deltaSubtypeTurn(sessionId, signal);
      } else if (turnIndex === 0 && toolProgressTest) {
        yield* toolProgressTurn(sessionId, signal);
      } else if (turnIndex === 0 && nonStreamedTurnTest) {
        yield* nonStreamedTurn(sessionId, signal);
      } else if (turnIndex === 0 && toolRaceTest) {
        yield* racingToolTurn(sessionId, signal);
      } else if (turnIndex === 0 && userNoFlushTest) {
        yield* userNoFlushTurn(sessionId, signal);
      } else if (
        turnIndex === 0 &&
        (refusalFallbackTest || promptText.includes(REFUSAL_FALLBACK_SENTINEL))
      ) {
        yield* refusalFallbackTurn(sessionId, signal);
      } else if (turnIndex === 0 && promptText.includes(PROVISIONAL_SENTINEL)) {
        yield* provisionalDeltaTurn(sessionId, signal);
      } else if (
        turnIndex === 0 &&
        promptText.includes(REFUSAL_NO_FALLBACK_SENTINEL)
      ) {
        yield* refusalNoFallbackTurn(sessionId, signal);
      } else if (turnIndex === 0 && autoResumeTest) {
        yield* autoResumeTurn(
          sessionId,
          signal,
          autoResumeGateFile,
          autoResumeHang,
        );
      } else if (turnIndex === 0) {
        yield* showcaseTurn(sessionId, signal);
      } else {
        yield* echoTurn(promptText, sessionId, turnIndex, signal);
      }
      turnIndex++;
    }
  }
}
