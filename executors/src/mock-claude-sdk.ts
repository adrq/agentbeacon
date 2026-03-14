// Mock replacement for @anthropic-ai/claude-agent-sdk's query() function.
// Activated when AGENTBEACON_MOCK_SDK=1 is set. The entire SDK is replaced —
// the executor's own code (command queue, event emission, abort handling) runs unchanged.

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
  | { type: "assistant"; message: { content: ContentBlock[] } }
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
    }
  | {
      type: "stream_event";
      event: {
        type: string;
        index?: number;
        delta?: { type: string; text?: string; thinking?: string };
      };
    };

// Transient failure simulation — counter persists for executor lifetime (cumulative
// across sessions). When set, the first N calls to query() throw an AxiosError-like
// error before yielding any messages.
let queryCallCount = 0;
const transientFailureCount = parseInt(
  process.env.AGENTBEACON_MOCK_SDK_TRANSIENT_FAILURES ?? "0",
  10,
);

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function abortError(): Error {
  const err = new Error("Aborted");
  err.name = "AbortError";
  return err;
}

function checkAbort(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw abortError();
}

// Wrap an async iterable so that iteration rejects with AbortError when the
// signal fires.  Needed because `for await` on the prompt stream blocks
// between turns and never checks the signal on its own.
async function* abortAwareIterable<T>(
  iterable: AsyncIterable<T>,
  signal: AbortSignal | undefined,
): AsyncGenerator<T> {
  if (!signal) {
    yield* iterable;
    return;
  }

  const iterator = iterable[Symbol.asyncIterator]();
  // Single abort promise + listener for the entire iteration lifecycle.
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

// Showcase scenario — exercises all renderer types the frontend supports.
async function* showcaseTurn(
  sessionId: string,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] showcaseTurn starting\n`);
  // 1a. Streaming thinking deltas (before complete thinking block)
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

  // 1b. Complete thinking block
  checkAbort(signal);
  await delay(80);
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

  // 2. Text + tool_use: Read
  checkAbort(signal);
  await delay(120);
  yield {
    type: "assistant",
    message: {
      content: [
        { type: "text", text: "I'll start by reading the configuration file." },
        {
          type: "tool_use",
          id: "toolu_mock_001",
          name: "Read",
          input: { file_path: "/workspace/src/config.rs" },
        },
      ],
    },
  };

  // 3. tool_result for Read
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

  // 4. Text + tool_use: Grep
  checkAbort(signal);
  await delay(120);
  yield {
    type: "assistant",
    message: {
      content: [
        { type: "text", text: "Now searching for TODO/FIXME items..." },
        {
          type: "tool_use",
          id: "toolu_mock_002",
          name: "Grep",
          input: { pattern: "TODO|FIXME", path: "/workspace/src" },
        },
      ],
    },
  };

  // 5. tool_result for Grep
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

  // 6. Thinking block (second)
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

  // 7. tool_use: Edit
  checkAbort(signal);
  await delay(120);
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

  // 8. tool_result for Edit
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

  // 8b. TodoWrite — task checklist snapshot
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

  // 8c. tool_result for TodoWrite
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

  // 8d. Streaming text deltas (simulating real-time text generation)
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

  // 9. Final markdown summary
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
    },
  };

  // 10. Result
  checkAbort(signal);
  yield {
    type: "result",
    subtype: "success",
    result: "Fixed 2 TODO/FIXME items.",
    total_cost_usd: 0.042,
    num_turns: 1,
    duration_ms: 4500,
  };
}

// Subsequent turns: simple echo response.
async function* echoTurn(
  prompt: string,
  sessionId: string,
  turnIndex: number,
  signal: AbortSignal | undefined,
): AsyncGenerator<MockSDKMessage, void> {
  process.stderr.write(`[mock-claude-sdk] echoTurn #${turnIndex}\n`);
  checkAbort(signal);
  await delay(50);
  yield {
    type: "assistant",
    message: {
      content: [{ type: "text", text: `Acknowledged: ${prompt}` }],
    },
  };

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

  // Init message
  yield {
    type: "system",
    subtype: "init",
    session_id: sessionId,
    mcp_servers: [],
  };

  if (typeof params.prompt === "string") {
    // Single-shot (not used by executor in practice, but handle for completeness)
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

      if (turnIndex === 0) {
        yield* showcaseTurn(sessionId, signal);
      } else {
        yield* echoTurn(promptText, sessionId, turnIndex, signal);
      }
      turnIndex++;
    }
  }
}
