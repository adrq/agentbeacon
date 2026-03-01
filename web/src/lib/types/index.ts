export type ExecutionStatus =
  | 'submitted' | 'working' | 'input-required'
  | 'completed' | 'failed' | 'canceled';

export type SessionStatus = ExecutionStatus;
export type EventType = 'message' | 'state_change' | 'platform';
export type Theme = 'light' | 'dark';
export type Screen = 'Home' | 'ExecutionDetail' | 'Projects' | 'ProjectDetail' | 'Agents';
export type AgentType = 'claude_sdk' | 'codex_sdk' | 'copilot_sdk' | 'opencode_sdk' | 'acp' | 'a2a';

export interface Project {
  id: string;
  name: string;
  path: string;
  default_agent_id: string | null;
  settings: Record<string, unknown>;
  is_git: boolean;
  created_at: string;
  updated_at: string;
}

export interface Driver {
  id: string;
  name: string;
  platform: string;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Agent {
  id: string;
  name: string;
  description: string | null;
  agent_type: AgentType;
  driver_id: string | null;
  enabled: boolean;
  config: Record<string, unknown>;
  sandbox_config: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface Execution {
  id: string;
  project_id: string | null;
  parent_execution_id: string | null;
  context_id: string;
  worktree_path: string | null;
  status: ExecutionStatus;
  title: string | null;
  input: string;
  metadata: Record<string, unknown>;
  max_depth: number;
  max_width: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

// GET /api/executions/{id} — wrapped execution + sessions
export interface ExecutionDetail {
  execution: Execution;
  sessions: SessionSummary[];
}

// Sessions from execution detail endpoint and GET /api/sessions
export interface SessionSummary {
  id: string;
  execution_id: string;
  parent_session_id: string | null;
  agent_id: string;
  agent_session_id: string | null;
  cwd: string | null;
  status: SessionStatus;
  coordination_mode: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

// Full session from GET /api/sessions — same shape as SessionSummary
export type Session = SessionSummary;

// Platform events use the same shape as message events (role + parts with data payloads)
export type PlatformPayload = MessagePayload;

// GET /api/sessions/{id}/events
export interface Event {
  id: number;
  execution_id: string;
  session_id: string | null;
  event_type: EventType;
  payload: MessagePayload | StateChangePayload | PlatformPayload;
  created_at: string;
}

export interface MessagePayload {
  role: 'user' | 'agent';
  parts: MessagePart[];
}

export type MessagePart =
  | { kind: 'text'; text: string }
  | { kind: 'data'; data: DataPartPayload }
  | { kind: 'file'; file: { name: string }; mimeType?: string }
  | { kind: string; [key: string]: unknown };

export type DataPartPayload =
  | EscalateData
  | DelegateData
  | HandoffResultData
  | TurnCompleteData
  | ToolCallActivityData
  | ToolCallUpdateData
  | ThinkingData
  | PlanData
  | { type: string; [key: string]: unknown };

export interface EscalateData {
  type: 'escalate';
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
  type: 'delegate';
  agent: string;
  child_session_id: string;
  prompt: string;
}

export interface HandoffResultData {
  type: 'handoff_result';
  child_session_id: string;
  message: string;
}

export interface TurnCompleteData {
  type: 'turn_complete';
  child_session_id: string;
  message: string;
}

export interface ToolCallActivityData {
  type: 'tool_call';
  toolCallId: string;
  title: string;
  status?: string;
  kind?: string;
}

export interface ToolCallUpdateData {
  type: 'tool_call_update';
  toolCallId: string;
  title?: string;
  status?: string;
}

export interface ThinkingData {
  type: 'agent_thought_chunk';
  text: string;
}

export interface PlanData {
  type: 'plan';
  entries: unknown[];
}

export interface StateChangePayload {
  from: string | null;
  to: string;
  error?: string;
  stderr?: string;
}

// Response types
export interface CreateExecutionResponse {
  execution: Execution;
  session_id: string;
  warning?: string;
}

export interface PostMessageResponse {
  event_id: number;
  session_status: string;
  execution_status: string;
}

// Type guards
export function isMessagePayload(p: MessagePayload | StateChangePayload): p is MessagePayload {
  return 'role' in p && 'parts' in p;
}

export function isStateChangePayload(p: MessagePayload | StateChangePayload): p is StateChangePayload {
  return 'to' in p && !('role' in p);
}

export function isEscalateData(d: DataPartPayload): d is EscalateData {
  return d.type === 'escalate';
}

export function isDelegateData(d: DataPartPayload): d is DelegateData {
  return d.type === 'delegate';
}

export function isHandoffResultData(d: DataPartPayload): d is HandoffResultData {
  return d.type === 'handoff_result';
}

export function isTurnCompleteData(d: DataPartPayload): d is TurnCompleteData {
  return d.type === 'turn_complete';
}

export function isToolCallActivity(d: DataPartPayload): d is ToolCallActivityData {
  return d.type === 'tool_call';
}

export function isToolCallUpdate(d: DataPartPayload): d is ToolCallUpdateData {
  return d.type === 'tool_call_update';
}

export function isThinkingData(d: DataPartPayload): d is ThinkingData {
  return d.type === 'agent_thought_chunk';
}

export function isPlanData(d: DataPartPayload): d is PlanData {
  return d.type === 'plan';
}

// Wiki types
export interface WikiPage {
  id: string;
  project_id: string;
  slug: string;
  title: string;
  body: string;
  revision_number: number;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface WikiPageListItem {
  slug: string;
  title: string;
  revision_number: number;
  updated_by: string | null;
  updated_at: string;
}

export interface WikiRevision {
  revision_number: number;
  title: string;
  body: string;
  summary: string | null;
  created_by: string | null;
  created_at: string;
}

export interface WikiRevisionListItem {
  revision_number: number;
  title: string;
  summary: string | null;
  created_by: string | null;
  created_at: string;
}

export interface PutWikiPageRequest {
  title: string;
  body: string;
  revision_number?: number | null;
  summary?: string;
}

// GET /api/executions/{id}/agents — session-level discovery
export interface AgentDiscoveryEntry {
  name: string;
  agent_name: string;
  session_id: string;
  status: string;
  parent_name: string | null;
}
