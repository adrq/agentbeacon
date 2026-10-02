// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

type MockSessionEvent =
  | { type: "assistant.message"; data: { messageId?: string; content: string } }
  | {
      type: "tool.execution_start";
      data: { toolCallId: string; toolName: string; arguments?: unknown };
    }
  | {
      type: "tool.execution_complete";
      data: {
        toolCallId: string;
        success: boolean;
        result?: { content: string; contents?: unknown[] };
        error?: { message: string; code?: string };
      };
    }
  | {
      type: "assistant.reasoning";
      data: { reasoningId?: string; content: string };
    }
  | {
      type: "assistant.reasoning_delta";
      data: { reasoningId?: string; deltaContent: string };
    }
  | {
      type: "session.error";
      data: { errorType: string; message: string };
    }
  | {
      type: "assistant.message_delta";
      data: {
        messageId?: string;
        deltaContent: string;
        totalResponseSizeBytes?: number;
      };
    }
  | { type: "session.idle"; data: Record<string, never> };

type MockEventType = MockSessionEvent["type"];
type MockEventPayload<T extends MockEventType> = Extract<
  MockSessionEvent,
  { type: T }
>;
type TypedHandler<T extends MockEventType> = (
  event: MockEventPayload<T>,
) => void;
type CatchAllHandler = (event: MockSessionEvent) => void;

const delay = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

const startupDelayMs = parseInt(
  process.env.AGENTBEACON_MOCK_COPILOT_STARTUP_DELAY_MS ?? "0",
  10,
);

let sessionCounter = 0;

class MockCopilotSession {
  readonly sessionId: string;
  private _handlers = new Map<string, Set<CatchAllHandler>>();
  private _aborted = false;
  private _turnIndex = 0;
  private _inFlight = false;
  private _pendingPrompts: Array<{ prompt: string; mode?: string }> = [];

  constructor() {
    this.sessionId = `mock-copilot-session-${++sessionCounter}`;
    process.stderr.write(
      `[mock-copilot-sdk] session created: ${this.sessionId}\n`,
    );
  }

  on<K extends MockEventType>(
    eventType: K,
    handler: TypedHandler<K>,
  ): () => void;
  on(handler: CatchAllHandler): () => void;
  on(
    eventTypeOrHandler: MockEventType | CatchAllHandler,
    handler?: CatchAllHandler,
  ): () => void {
    if (typeof eventTypeOrHandler === "function") {
      const key = "*";
      if (!this._handlers.has(key)) this._handlers.set(key, new Set());
      this._handlers.get(key)!.add(eventTypeOrHandler);
      return () => this._handlers.get(key)?.delete(eventTypeOrHandler);
    }
    const key = eventTypeOrHandler;
    const h = handler as CatchAllHandler;
    if (!this._handlers.has(key)) this._handlers.set(key, new Set());
    this._handlers.get(key)!.add(h);
    return () => this._handlers.get(key)?.delete(h);
  }

  private dispatch(event: MockSessionEvent): void {
    const typed = this._handlers.get(event.type);
    if (typed) for (const h of typed) h(event);
    const catchAll = this._handlers.get("*");
    if (catchAll) for (const h of catchAll) h(event);
  }

  async send(options: {
    prompt: string;
    attachments?: { type: string; path: string }[];
    mode?: "enqueue" | "immediate";
  }): Promise<string> {
    if (options.attachments?.length) {
      process.stderr.write(
        `[mock-copilot-sdk] attachments=${JSON.stringify(options.attachments.map((a) => a.path))}\n`,
      );
    }
    process.stderr.write(
      `[mock-copilot-sdk] send turn=${this._turnIndex} prompt=${JSON.stringify(options.prompt)}\n`,
    );

    if (this._inFlight) {
      process.stderr.write(
        `[mock-copilot-sdk] queued mid-turn prompt=${JSON.stringify(options.prompt)}\n`,
      );
      this._pendingPrompts.push({ prompt: options.prompt, mode: options.mode });
      return `msg-queued-${this._turnIndex}`;
    }

    if (this._aborted) {
      this._aborted = false;
      this.dispatch({ type: "session.idle", data: {} });
      return `msg-${this._turnIndex}`;
    }

    await delay(50);

    if (this._aborted) {
      this._aborted = false;
      this.dispatch({ type: "session.idle", data: {} });
      return `msg-${this._turnIndex}`;
    }

    if (options.prompt === "__empty_turn__") {
      this._turnIndex++;
      this.dispatch({ type: "session.idle", data: {} });
      return `msg-${this._turnIndex - 1}`;
    }

    if (options.prompt === "__fatal_session_error__") {
      this._turnIndex++;
      this.dispatch({
        type: "session.error",
        data: {
          errorType: "connection_closed",
          message: "WebSocket connection closed unexpectedly",
        },
      });
      return `msg-${this._turnIndex - 1}`;
    }

    if (options.prompt === "__send_reject__") {
      throw new Error("mock send rejected before idle");
    }

    if (options.prompt === "__slow_turn__") {
      this._inFlight = true;
      await this.slowTurnScenario();
      this._turnIndex++;
      while (this._pendingPrompts.length > 0 && !this._aborted) {
        const queued = this._pendingPrompts.shift()!;
        this.echoScenario(queued.prompt);
        this._turnIndex++;
      }
      this._pendingPrompts.length = 0;
      this._aborted = false;
      this._inFlight = false;
      this.dispatch({ type: "session.idle", data: {} });
      return `msg-${this._turnIndex - 1}`;
    }

    this._inFlight = true;

    if (this._turnIndex === 0) {
      this.showcaseScenario();
    } else {
      this.echoScenario(options.prompt);
    }
    this._turnIndex++;

    while (this._pendingPrompts.length > 0 && !this._aborted) {
      const queued = this._pendingPrompts.shift()!;
      this.echoScenario(queued.prompt);
      this._turnIndex++;
    }
    this._pendingPrompts.length = 0;
    this._inFlight = false;

    this.dispatch({ type: "session.idle", data: {} });
    return `msg-${this._turnIndex - 1}`;
  }

  async sendAndWait(options: {
    prompt: string;
  }): Promise<{ data: { content: string } } | undefined> {
    process.stderr.write(
      `[mock-copilot-sdk] sendAndWait turn=${this._turnIndex}\n`,
    );
    let lastContent: string | undefined;
    const unsub = this.on(
      "assistant.message",
      (event: MockEventPayload<"assistant.message">) => {
        lastContent = event.data.content;
      },
    );
    await this.send(options);
    unsub();
    return lastContent !== undefined
      ? { data: { content: lastContent } }
      : undefined;
  }

  private showcaseScenario(): void {
    this.dispatch({
      type: "assistant.reasoning_delta",
      data: { deltaContent: "I need to understand" },
    });
    this.dispatch({
      type: "assistant.reasoning_delta",
      data: { deltaContent: " the test structure first." },
    });
    this.dispatch({
      type: "assistant.reasoning",
      data: {
        content:
          "I need to understand the test structure first. Let me find the test files.",
      },
    });

    this.dispatch({
      type: "tool.execution_start",
      data: {
        toolCallId: "call_001",
        toolName: "Bash",
        arguments: { command: "find /workspace/tests -name '*.py'" },
      },
    });
    this.dispatch({
      type: "tool.execution_complete",
      data: {
        toolCallId: "call_001",
        success: true,
        result: {
          content: "/workspace/tests/test_main.py",
          contents: [
            {
              type: "terminal",
              text: "/workspace/tests/test_main.py",
              exitCode: 0,
              cwd: "/workspace",
            },
          ],
        },
      },
    });

    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: "Found test files" },
    });
    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: " in /workspace/tests/." },
    });

    this.dispatch({
      type: "assistant.message",
      data: {
        content:
          "Found test files in /workspace/tests/. Let me read and fix the failing test.",
      },
    });

    this.dispatch({
      type: "assistant.reasoning_delta",
      data: { deltaContent: "The test_validate_port test" },
    });
    this.dispatch({
      type: "assistant.reasoning_delta",
      data: { deltaContent: " expects port validation." },
    });
    this.dispatch({
      type: "assistant.reasoning",
      data: {
        content:
          "The test_validate_port test expects port validation. I need to implement it.",
      },
    });

    this.dispatch({
      type: "tool.execution_start",
      data: {
        toolCallId: "call_002",
        toolName: "Read",
        arguments: { file_path: "/workspace/tests/test_main.py" },
      },
    });
    this.dispatch({
      type: "tool.execution_complete",
      data: {
        toolCallId: "call_002",
        success: true,
        result: { content: "def test_validate_port(): ..." },
      },
    });

    this.dispatch({
      type: "tool.execution_start",
      data: { toolCallId: "call_003", toolName: "Write" },
    });
    this.dispatch({
      type: "tool.execution_complete",
      data: {
        toolCallId: "call_003",
        success: false,
        error: { message: "Permission denied: /etc/readonly-file" },
      },
    });

    this.dispatch({
      type: "tool.execution_start",
      data: {
        toolCallId: "call_004",
        toolName: "Bash",
        arguments: { command: "make test" },
      },
    });
    this.dispatch({
      type: "tool.execution_complete",
      data: {
        toolCallId: "call_004",
        success: false,
        error: { message: "Command exited with code 1" },
        result: {
          content: "FAILED tests/test_main.py::test_validate_port",
          contents: [
            {
              type: "terminal",
              text: "FAILED tests/test_main.py::test_validate_port\n1 failed in 0.04s",
              exitCode: 1,
              cwd: "/workspace",
            },
          ],
        },
      },
    });

    this.dispatch({
      type: "session.error",
      data: {
        errorType: "permission_denied",
        message: "Tool execution not permitted: DeleteFile",
      },
    });

    this.dispatch({
      type: "assistant.reasoning_delta",
      data: { deltaContent: "Now that I see the test output" },
    });
    this.dispatch({
      type: "assistant.reasoning_delta",
      data: { deltaContent: ", I can fix the assertion." },
    });
    this.dispatch({
      type: "assistant.reasoning",
      data: {
        content: "Now that I see the test output, I can fix the assertion.",
      },
    });

    const finalContent = [
      "Fixed the failing test by implementing port validation.",
      "",
      "```",
      "4 passed in 0.12s",
      "```",
    ].join("\n");

    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: "Fixed the failing" },
    });
    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: " test by implementing port validation." },
    });

    this.dispatch({
      type: "assistant.message",
      data: { content: finalContent },
    });
  }

  private async slowTurnScenario(): Promise<void> {
    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: "Working on it..." },
    });
    await delay(200);
    if (this._aborted) return;
    this.dispatch({
      type: "assistant.message",
      data: { content: "First turn response" },
    });
  }

  private echoScenario(prompt: string): void {
    const content = `Acknowledged: ${prompt}`;
    this.dispatch({
      type: "assistant.message",
      data: { content },
    });
  }

  async abort(): Promise<void> {
    this._aborted = true;
  }

  async disconnect(): Promise<void> {
    this._handlers.clear();
  }
}

export class CopilotClient {
  constructor(_options?: Record<string, unknown>) {
    process.stderr.write(`[mock-copilot-sdk] CopilotClient created\n`);
  }

  async start(): Promise<void> {}

  async createSession(
    _config?: Record<string, unknown>,
  ): Promise<MockCopilotSession> {
    if (startupDelayMs > 0) {
      await delay(startupDelayMs);
    }
    process.stderr.write(`[mock-copilot-sdk] resume=false\n`);
    const excludedTools = _config?.excludedTools;
    if (Array.isArray(excludedTools) && excludedTools.length > 0) {
      process.stderr.write(
        `[mock-copilot-sdk] excludedTools=${JSON.stringify(excludedTools)}\n`,
      );
    } else {
      process.stderr.write(
        `[mock-copilot-sdk] WARNING: no excludedTools configured\n`,
      );
    }
    const systemMsg = _config?.systemMessage as
      | { mode: string; content?: string }
      | undefined;
    if (systemMsg?.content) {
      process.stderr.write(
        `[mock-copilot-sdk] system_prompt_present=true, system_prompt_len=${systemMsg.content.length}\n`,
      );
      const hasAgentBeaconBriefing = systemMsg.content.includes(
        "# AgentBeacon Environment",
      );
      process.stderr.write(
        `[mock-copilot-sdk] system_prompt_has_briefing=${hasAgentBeaconBriefing}\n`,
      );
    } else {
      process.stderr.write(`[mock-copilot-sdk] system_prompt_present=false\n`);
    }
    return new MockCopilotSession();
  }

  async resumeSession(
    _sessionId: string,
    _config?: Record<string, unknown>,
  ): Promise<MockCopilotSession> {
    if (startupDelayMs > 0) {
      await delay(startupDelayMs);
    }
    process.stderr.write(
      `[mock-copilot-sdk] resume=true, resumeSessionId=${_sessionId}\n`,
    );
    const excludedTools = _config?.excludedTools;
    if (Array.isArray(excludedTools) && excludedTools.length > 0) {
      process.stderr.write(
        `[mock-copilot-sdk] excludedTools=${JSON.stringify(excludedTools)}\n`,
      );
    } else {
      process.stderr.write(
        `[mock-copilot-sdk] WARNING: no excludedTools configured\n`,
      );
    }
    const systemMsg = _config?.systemMessage as
      | { mode: string; content?: string }
      | undefined;
    if (systemMsg?.content) {
      process.stderr.write(
        `[mock-copilot-sdk] system_prompt_present=true, system_prompt_len=${systemMsg.content.length}\n`,
      );
      const hasAgentBeaconBriefing = systemMsg.content.includes(
        "# AgentBeacon Environment",
      );
      process.stderr.write(
        `[mock-copilot-sdk] system_prompt_has_briefing=${hasAgentBeaconBriefing}\n`,
      );
    } else {
      process.stderr.write(`[mock-copilot-sdk] system_prompt_present=false\n`);
    }
    return new MockCopilotSession();
  }

  async stop(): Promise<Error[]> {
    return [];
  }
}
