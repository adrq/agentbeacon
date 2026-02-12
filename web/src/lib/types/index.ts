export type ExecutionStatus =
  | 'submitted' | 'working' | 'input-required'
  | 'completed' | 'failed' | 'canceled';

export type SessionStatus = ExecutionStatus;
export type CoordinationMode = 'sdk' | 'mcp_poll';
export type EventType = 'message' | 'state_change';
export type Theme = 'light' | 'dark';
export type Screen = 'Home' | 'ExecutionDetail';

export interface Agent {
  id: string;
  name: string;
  description: string | null;
  agent_type: string;
  enabled: boolean;
  config: Record<string, unknown>;
  sandbox_config: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface Execution {
  id: string;
  workspace_id: string | null;
  parent_execution_id: string | null;
  context_id: string;
  status: ExecutionStatus;
  title: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

// GET /api/executions/{id} — flat execution fields + sessions[]
export type ExecutionDetail = Execution & {
  sessions: SessionSummary[];
};

// Sessions from execution detail endpoint (fewer fields than full session)
export interface SessionSummary {
  id: string;
  execution_id: string;
  parent_session_id: string | null;
  agent_id: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
}

// Full session from GET /api/sessions
export interface Session {
  id: string;
  execution_id: string;
  parent_session_id: string | null;
  agent_id: string;
  agent_session_id: string | null;
  status: SessionStatus;
  coordination_mode: CoordinationMode;
  created_at: string;
  updated_at: string;
}

// GET /api/sessions/{id}/events
export interface Event {
  id: number;
  event_type: EventType;
  payload: MessagePayload | StateChangePayload;
  created_at: string;
}

export interface MessagePayload {
  role: 'user' | 'agent';
  parts: MessagePart[];
}

export type MessagePart =
  | { kind: 'text'; text: string }
  | { kind: 'data'; data: ToolCallData }
  | { kind: 'file'; file: { name: string }; mimeType?: string };

export type ToolCallData =
  | AskUserData
  | DelegateData
  | HandoffResultData
  | { tool: string; [key: string]: unknown };

export interface AskUserData {
  tool: 'ask_user';
  batch_id: string;
  batch_size: number;
  batch_index: number;
  question: string;
  context?: string;
  options?: QuestionOption[];
  importance: 'blocking' | 'fyi';
}

export interface QuestionOption {
  label: string;
  description: string;
}

export interface DelegateData {
  tool: 'delegate';
  agent: string;
  child_session_id: string;
  prompt: string;
}

export interface HandoffResultData {
  tool: 'handoff_result';
  child_session_id: string;
  message: string;
}

export interface StateChangePayload {
  from: string | null;
  to: string;
}

// Type guards
export function isMessagePayload(p: MessagePayload | StateChangePayload): p is MessagePayload {
  return 'role' in p && 'parts' in p;
}

export function isStateChangePayload(p: MessagePayload | StateChangePayload): p is StateChangePayload {
  return 'to' in p && !('role' in p);
}

export function isAskUserData(d: ToolCallData): d is AskUserData {
  return d.tool === 'ask_user';
}

export function isDelegateData(d: ToolCallData): d is DelegateData {
  return d.tool === 'delegate';
}

export function isHandoffResultData(d: ToolCallData): d is HandoffResultData {
  return d.tool === 'handoff_result';
}
