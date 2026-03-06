import type { AgentType } from './types';

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
        status: 'completed',
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
      return {
        normalized: 'thinking',
        text: (raw.thinking as string) ?? '',
      };
    default:
      return { normalized: 'unknown', raw };
  }
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
