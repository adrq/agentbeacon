// Mock replacement for @github/copilot-sdk's CopilotClient + CopilotSession.
// Activated when AGENTBEACON_MOCK_SDK=1 is set. The entire SDK is replaced —
// the executor's own code (event handlers, pendingBlocks, abort handling) runs unchanged.

// Event types matching the subset of SessionEvent that the executor uses.
// Structurally compatible with @github/copilot-sdk's generated SessionEvent union.
type MockSessionEvent =
  | { type: "assistant.message"; data: { messageId?: string; content: string } }
  | {
      type: "tool.execution_start";
      data: { toolCallId: string; toolName: string };
    }
  | {
      type: "tool.execution_complete";
      data: { toolCallId: string; success?: boolean };
    }
  | {
      type: "assistant.reasoning";
      data: { reasoningId?: string; content: string };
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
    };

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

let sessionCounter = 0;

class MockCopilotSession {
  readonly sessionId: string;
  private _handlers = new Map<string, Set<CatchAllHandler>>();
  private _aborted = false;
  private _turnIndex = 0;

  constructor() {
    this.sessionId = `mock-copilot-session-${++sessionCounter}`;
    process.stderr.write(
      `[mock-copilot-sdk] session created: ${this.sessionId}\n`,
    );
  }

  // Typed overload matching real SDK's on<K>(eventType, handler)
  on<K extends MockEventType>(
    eventType: K,
    handler: TypedHandler<K>,
  ): () => void;
  // Catch-all overload matching real SDK's on(handler)
  on(handler: CatchAllHandler): () => void;
  on(
    eventTypeOrHandler: MockEventType | CatchAllHandler,
    handler?: CatchAllHandler,
  ): () => void {
    if (typeof eventTypeOrHandler === "function") {
      // Catch-all form: on(handler)
      const key = "*";
      if (!this._handlers.has(key)) this._handlers.set(key, new Set());
      this._handlers.get(key)!.add(eventTypeOrHandler);
      return () => this._handlers.get(key)?.delete(eventTypeOrHandler);
    }
    // Typed form: on(eventType, handler)
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

  async sendAndWait(options: {
    prompt: string;
  }): Promise<{ data: { content: string } } | undefined> {
    process.stderr.write(
      `[mock-copilot-sdk] sendAndWait turn=${this._turnIndex}\n`,
    );
    if (this._aborted) {
      this._aborted = false;
      return undefined;
    }

    await delay(50);

    if (this._aborted) {
      this._aborted = false;
      return undefined;
    }

    let finalContent: string;

    if (this._turnIndex === 0) {
      finalContent = this.showcaseScenario();
    } else {
      finalContent = this.echoScenario(options.prompt);
    }
    this._turnIndex++;

    // Return only the LAST assistant.message content, matching real SDK behavior.
    return { data: { content: finalContent } };
  }

  // Showcase scenario: reasoning, tool calls, then final assistant message.
  // Events dispatched BEFORE sendAndWait returns so executor's on() handlers
  // accumulate pendingBlocks and flush on assistant.message.
  private showcaseScenario(): string {
    // --- Group 1: reasoning + tool ---
    this.dispatch({
      type: "assistant.reasoning",
      data: {
        content:
          "I need to understand the test structure first. Let me find the test files.",
      },
    });

    this.dispatch({
      type: "tool.execution_start",
      data: { toolCallId: "call_001", toolName: "Bash" },
    });
    this.dispatch({
      type: "tool.execution_complete",
      data: { toolCallId: "call_001" },
    });

    // Streaming deltas before flush
    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: "Found test files" },
    });
    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: " in /workspace/tests/." },
    });

    // Flush group 1: executor accumulates thinking + tool_use, then flushes on assistant.message
    this.dispatch({
      type: "assistant.message",
      data: {
        content:
          "Found test files in /workspace/tests/. Let me read and fix the failing test.",
      },
    });

    // --- Group 2: reasoning + tool + final message ---
    this.dispatch({
      type: "assistant.reasoning",
      data: {
        content:
          "The test_validate_port test expects port validation. I need to implement it.",
      },
    });

    this.dispatch({
      type: "tool.execution_start",
      data: { toolCallId: "call_002", toolName: "Read" },
    });
    this.dispatch({
      type: "tool.execution_complete",
      data: { toolCallId: "call_002" },
    });

    const finalContent = [
      "Fixed the failing test by implementing port validation.",
      "",
      "```",
      "4 passed in 0.12s",
      "```",
    ].join("\n");

    // Streaming deltas before flush
    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: "Fixed the failing" },
    });
    this.dispatch({
      type: "assistant.message_delta",
      data: { deltaContent: " test by implementing port validation." },
    });

    // Flush group 2
    this.dispatch({
      type: "assistant.message",
      data: { content: finalContent },
    });

    return finalContent;
  }

  // Subsequent turns: simple echo.
  private echoScenario(prompt: string): string {
    const content = `Acknowledged: ${prompt}`;
    this.dispatch({
      type: "assistant.message",
      data: { content },
    });
    return content;
  }

  async abort(): Promise<void> {
    this._aborted = true;
  }

  async destroy(): Promise<void> {
    this._handlers.clear();
  }
}

// Exported as CopilotClient so destructuring `const { CopilotClient } = await import(...)` works.
export class CopilotClient {
  constructor(_options?: Record<string, unknown>) {
    process.stderr.write(`[mock-copilot-sdk] CopilotClient created\n`);
  }

  async start(): Promise<void> {}

  async createSession(
    _config?: Record<string, unknown>,
  ): Promise<MockCopilotSession> {
    return new MockCopilotSession();
  }

  async resumeSession(
    _sessionId: string,
    _config?: Record<string, unknown>,
  ): Promise<MockCopilotSession> {
    return new MockCopilotSession();
  }

  async stop(): Promise<Error[]> {
    return [];
  }
}
