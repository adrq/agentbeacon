// JSON Lines protocol types between Rust worker and Node.js executor wrappers.

// --- Commands (Rust → Node, on stdin) ---

export interface StartCommand {
  type: "start";
  prompt: string;
  cwd: string;
  mcpServers?: Record<string, McpServerConfig>;
  model?: string;
  maxTurns?: number;
  maxBudgetUsd?: number;
  systemPrompt?: string;
  provider?: ProviderConfig;
  resumeSessionId?: string;
  thinking?: { type: string; budgetTokens?: number };
  effort?: string;
  reasoningEffort?: string;
}

// Intentionally loose type — Rust sends opaque JSON, SDK consumes it
export interface ProviderConfig {
  type: string;
  baseUrl?: string;
  // apiKey is NOT here — injected via process env, never over stdin
  [key: string]: unknown;
}

export interface McpServerConfig {
  type: string;
  // HTTP transport
  url?: string;
  headers?: Record<string, string>;
  // stdio transport
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  // Common
  tools?: string[];
}

export interface PromptCommand {
  type: "prompt";
  text: string;
}

export interface CancelCommand {
  type: "cancel";
}

export interface StopCommand {
  type: "stop";
}

// Synthetic — injected when stdin closes
export interface EofCommand {
  type: "eof";
}

export type Command =
  | StartCommand
  | PromptCommand
  | CancelCommand
  | StopCommand
  | EofCommand;

// --- Events (Node → Rust, on stdout) ---

export interface InitEvent {
  type: "init";
  sessionId: string;
  mcpServers?: Array<{ name: string; status: string }>;
}

export interface MessageEvent {
  type: "message";
  role: string;
  content: unknown[];
}

export interface ResultEvent {
  type: "result";
  subtype: string;
  sessionId?: string;
  result?: string;
  errors?: string[];
  costUsd?: number;
  numTurns?: number;
  durationMs?: number;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

export type Event = InitEvent | MessageEvent | ResultEvent | ErrorEvent;
