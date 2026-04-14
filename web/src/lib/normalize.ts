import type { AgentType, NormalizedText, NormalizedUsage } from './types';

export interface NormalizedToolCall {
  normalized: 'tool_call';
  toolCallId: string;
  title: string;
  status?: string;
  input?: unknown;
  kind?: string;
  content?: unknown[];
}

export interface NormalizedToolResult {
  normalized: 'tool_result';
  toolCallId: string;
  content?: string | unknown[];
  isError?: boolean;
}

export interface NormalizedThinking {
  normalized: 'thinking';
  text: string;
}

export type NormalizedData =
  | NormalizedToolCall
  | NormalizedToolResult
  | NormalizedThinking
  | NormalizedUsage
  | NormalizedText
  | { normalized: 'unknown'; raw: Record<string, unknown> };

export function normalizeDataPart(agentType: AgentType, raw: Record<string, unknown>): NormalizedData {
  const type = raw.type as string | undefined;

  // Platform events handled separately in ChatView
  if (type === 'escalate' || type === 'delegate' || type === 'turn_complete' || type === 'sender')
    return { normalized: 'unknown', raw };

  switch (agentType) {
    case 'claude_sdk':
    case 'copilot_sdk':
      return normalizeSdkPart(raw);
    case 'codex_sdk':
      return normalizeCodexPart(raw);
    case 'acp':
      return normalizeAcpPart(raw);
    default:
      return { normalized: 'unknown', raw };
  }
}

function normalizeSdkPart(raw: Record<string, unknown>): NormalizedData {
  switch (raw.type) {
    case 'tool_use':
      return {
        normalized: 'tool_call',
        toolCallId: (raw.id as string) ?? '',
        title: (raw.name as string) ?? '',
        status: 'running',
        input: raw.input,
      };
    case 'tool_result':
      return {
        normalized: 'tool_result',
        toolCallId: (raw.tool_use_id as string) ?? '',
        content: raw.content as string | unknown[] | undefined,
        isError: raw.is_error as boolean | undefined,
      };
    case 'thinking':
    case 'thinking_delta':
      return {
        normalized: 'thinking',
        text: (raw.thinking as string) ?? '',
      };
    case 'usage_update':
      return {
        normalized: 'usage',
        inputTokens: (raw.input_tokens as number) ?? 0,
        outputTokens: (raw.output_tokens as number) ?? 0,
      };
    case 'usage_snapshot':
      return {
        normalized: 'usage',
        inputTokens: (raw.input_tokens as number) ?? 0,
        outputTokens: (raw.output_tokens as number) ?? 0,
        modelContextWindow: (raw.context_window as number | undefined) ?? null,
      };
    default:
      return { normalized: 'unknown', raw };
  }
}

function normalizeCodexPart(raw: Record<string, unknown>): NormalizedData {
  // New shape: the catch-all wraps the full JSON-RPC notification as the data part,
  // so `raw` is `{method: "item/completed", params: {item: {...}}}`.
  // Backward compat: if `raw.method` is absent (old stored data), fall back to
  // the legacy `raw.type` based routing.
  const method = raw.method as string | undefined;
  const params = (raw.params ?? raw) as Record<string, unknown>;

  if (method === 'item/completed' || method === 'item/started') {
    const item = (params.item ?? params) as Record<string, unknown>;
    return normalizeCodexItem(item);
  }

  if (method === 'thread/tokenUsage/updated') {
    const usage = (params.tokenUsage ?? params.usage ?? params) as Record<string, unknown>;
    return normalizeCodexUsage(usage);
  }

  // Reasoning delta methods — extract text for thinking display
  if (method === 'item/reasoning/textDelta') {
    const rawDelta = params.delta;
    const text = typeof rawDelta === 'string' ? rawDelta : (rawDelta as any)?.text ?? '';
    return { normalized: 'thinking', text };
  }

  if (method === 'item/reasoning/summaryTextDelta') {
    const rawDelta = params.delta;
    const text = typeof rawDelta === 'string' ? rawDelta : (rawDelta as any)?.text ?? '';
    return { normalized: 'thinking', text };
  }

  // summaryPartAdded is a marker-only event (no delta field in schema)
  if (method === 'item/reasoning/summaryPartAdded') {
    return { normalized: 'thinking', text: '' };
  }

  // Legacy / backward compat: no method field — use type-based routing (old stored data)
  if (!method) {
    const type = raw.type as string | undefined;

    // Token usage events have no `type` — detected by presence of `tokenUsage`
    if (raw.tokenUsage != null) {
      return normalizeCodexUsage(raw.tokenUsage as Record<string, unknown>);
    }

    if (type) {
      return normalizeCodexItem(raw);
    }
  }

  // Fallback for other methods / unknown shapes
  return { normalized: 'unknown', raw };
}

/** Normalize a Codex item object (from item/completed or item/started params.item). */
function normalizeCodexItem(item: Record<string, unknown>): NormalizedData {
  const type = item.type as string | undefined;

  switch (type) {
    case 'commandExecution':
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        title: (item.command as string) ?? 'Shell command',
        status: item.status === 'inProgress' ? 'running' : item.status === 'completed' ? 'completed' : 'failed',
        input: { cwd: item.cwd, exitCode: item.exitCode },
      };
    case 'fileChange':
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        title: 'File change',
        status: item.status === 'inProgress' ? 'running' : item.status === 'completed' ? 'completed' : 'failed',
        input: { changes: item.changes },
      };
    case 'mcpToolCall':
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        title: `${(item.server as string) ?? 'mcp'}/${(item.tool as string) ?? 'unknown'}`,
        status: item.status === 'inProgress' ? 'running' : item.status === 'completed' ? 'completed' : 'failed',
        input: item.arguments,
        content: item.result != null ? [item.result] : undefined,
      };
    case 'dynamicToolCall':
      return {
        normalized: 'tool_call',
        toolCallId: (item.id as string) ?? '',
        title: (item.tool as string) ?? 'Dynamic tool',
        status: item.status === 'inProgress' ? 'running' : item.status === 'completed' ? 'completed' : 'failed',
        input: item.arguments,
      };
    case 'reasoning': {
      // Schema: ReasoningThreadItem has summary: string[] and content: string[].
      // Prefer summary; fall back to content when summary is absent.
      // The `typeof c === 'string'` path is the schema-correct one (string[]);
      // the `c?.text` fallback handles legacy stored data with {type, text} objects.
      let reasoningText = '';
      if (Array.isArray(item.summary) && item.summary.length > 0) {
        reasoningText = (item.summary as unknown[])
          .map(c => typeof c === 'string' ? c : (c as any)?.text ?? '')
          .join('\n');
      } else if (Array.isArray(item.content)) {
        reasoningText = (item.content as unknown[])
          .map(c => typeof c === 'string' ? c : (c as any)?.text ?? '')
          .join('\n');
      }
      return {
        normalized: 'thinking',
        text: reasoningText,
      };
    }
    case 'agentMessage': {
      // Schema: AgentMessageThreadItem has `text: string` (required).
      // No content fallback — `text` is the authoritative field.
      const agentText = (item.text as string) ?? '';
      return {
        normalized: 'text',
        text: agentText,
      };
    }
    default:
      return { normalized: 'unknown', raw: item };
  }
}

/** Normalize a Codex token usage object. */
function normalizeCodexUsage(usage: Record<string, unknown>): NormalizedData {
  const last = (usage.last ?? {}) as Record<string, number>;
  const total = (usage.total ?? {}) as Record<string, number>;
  // Support both nested format ({total: {inputTokens}}) and flat format ({totalInputTokens})
  const flatInput = (usage.totalInputTokens as number | undefined) ?? 0;
  const flatOutput = (usage.totalOutputTokens as number | undefined) ?? 0;
  return {
    normalized: 'usage',
    inputTokens: total.inputTokens ?? last.inputTokens ?? flatInput,
    outputTokens: total.outputTokens ?? last.outputTokens ?? flatOutput,
    modelContextWindow: (usage.modelContextWindow as number | undefined) ?? null,
  };
}

function normalizeAcpPart(raw: Record<string, unknown>): NormalizedData {
  switch (raw.type) {
    case 'tool_call':
      return {
        normalized: 'tool_call',
        toolCallId: (raw.toolCallId as string) ?? '',
        title: (raw.title as string) ?? '',
        status: raw.status === 'in_progress' ? 'running' : (raw.status as string | undefined),
        kind: raw.kind as string | undefined,
        content: raw.content as unknown[] | undefined,
      };
    case 'tool_call_update':
      return {
        normalized: 'tool_call',
        toolCallId: (raw.toolCallId as string) ?? '',
        title: (raw.title as string) ?? '',
        status: raw.status === 'in_progress' ? 'running' : (raw.status as string | undefined),
        content: raw.content as unknown[] | undefined,
      };
    case 'agent_thought_chunk':
      return {
        normalized: 'thinking',
        text: (raw.text as string) ?? '',
      };
    default:
      return { normalized: 'unknown', raw };
  }
}
