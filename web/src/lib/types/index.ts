
/** Intent field on executions and sessions */
export type ExecutionDesired = 'run' | 'terminate';
export type SessionDesired = 'run' | 'stop' | 'terminate';

/** Executor state (session only) */
export type ExecutorState = 'unassigned' | 'running' | 'idle' | 'crashed';

/** Terminal outcome — null while still active */
export type Outcome = 'completed' | 'canceled' | 'failed';

/** Computed display status for executions */
export type ExecutionDisplayStatus =
  | 'working' | 'awaiting_input'
  | 'completed' | 'canceled' | 'failed';

/** Computed display status for sessions */
export type SessionDisplayStatus =
  | 'working' | 'idle' | 'stopped' | 'unassigned' | 'crashed'
  | 'completed' | 'canceled' | 'failed';

// Keep legacy aliases — many components use these as the prop type
export type ExecutionStatus = ExecutionDisplayStatus;
export type SessionStatus = SessionDisplayStatus;

export type EventType = 'message' | 'state_change' | 'platform';
export type Theme = 'light' | 'dark';
export type RouteMode = 'view' | 'new' | 'edit';
export type NavSection = 'home' | 'executions' | 'projects' | 'agents' | 'wiki' | 'settings';
export type AgentType = 'claude_sdk' | 'codex_sdk' | 'copilot_sdk' | 'acp';

export interface Project {
  id: string;
  name: string;
  path: string;
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
  system_prompt: string | null;
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
  desired: ExecutionDesired;
  outcome: Outcome | null;
  status: ExecutionDisplayStatus;
  completion_eligible: boolean;
  title: string | null;
  metadata: Record<string, unknown>;
  max_depth: number;
  max_width: number;
  sandbox_policy: { fs_level: string };
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
  worktree_path: string | null;
  base_commit_sha: string | null;
  desired: SessionDesired;
  executor_state: ExecutorState;
  outcome: Outcome | null;
  status: SessionDisplayStatus;
  desired_by: string | null;
  worker_id: string | null;
  command_type: string | null;
  parent_notified: boolean;
  recovery_attempts: number;
  metadata: Record<string, unknown>;
  sandbox_policy: { fs_level: string };
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

// Full session from GET /api/sessions — same shape as SessionSummary
export type Session = SessionSummary;

// GET /api/sessions/{id}/worktree
export interface WorktreeInfo {
  path: string;
  branch: string | null;
  head_sha: string | null;
  exists: boolean;
}

// GET /api/sessions/{id}/worktree/branches
export interface BranchInfo {
  name: string;
  is_default: boolean;
}

export interface BranchesResponse {
  branches: BranchInfo[];
  current_branch: string | null;
}

// GET /api/sessions/{id}/worktree/diff
export interface DiffFileEntry {
  path: string;
  status: string;      // M, A, D
  insertions: number;
  deletions: number;
}

export interface DiffSummary {
  files_changed: number;
  insertions: number;
  deletions: number;
}

export interface DiffCommitEntry {
  sha: string;
  message: string;
  author: string;
  date: string;
}

export interface DiffResponse {
  files: DiffFileEntry[];
  summary: DiffSummary;
  patch?: string;       // omitted when stat=true
  truncated?: boolean;  // true on 413 responses (patch > 1MB)
  commits?: DiffCommitEntry[];  // commits between base and HEAD
  content_identical?: boolean;  // true when branch content matches HEAD
}

// Platform events use the same shape as message events (role + parts with data payloads)
export type PlatformPayload = MessagePayload;

// GET /api/sessions/{id}/events
export interface Event {
  id: number;
  execution_id: string;
  session_id: string | null;
  event_type: EventType;
  payload: MessagePayload | StateChangePayload | PlatformPayload;
  msg_seq?: number;
  created_at: string;
}

// Ephemeral streaming event (SSE-only, not persisted)
export interface EphemeralEvent {
  session_id: string;
  msg_seq: number;
  payload: MessagePayload;
}

export interface MessagePayload {
  role: 'ROLE_USER' | 'ROLE_AGENT';
  parts: MessagePart[];
}

export type MessagePart =
  | { text: string; mediaType?: string; filename?: string; metadata?: Record<string, unknown> }
  | { data: DataPartPayload; mediaType?: string; filename?: string; metadata?: Record<string, unknown> }
  | { url: string; mediaType?: string; filename?: string; metadata?: Record<string, unknown> }
  | { raw: string; mediaType?: string; filename?: string; metadata?: Record<string, unknown> };

export function isTextPart(p: MessagePart): p is { text: string } & MessagePart {
  return 'text' in p;
}

export function isDataPart(p: MessagePart): p is { data: DataPartPayload } & MessagePart {
  return 'data' in p;
}

export function isFilePart(p: MessagePart): p is ({ url: string } | { raw: string }) & MessagePart {
  return 'url' in p || 'raw' in p;
}

export interface SenderData {
  type: 'sender';
  name: string;
  session_id: string;
}

export type DataPartPayload =
  | EscalateData
  | DelegateData
  | TurnCompleteData
  | ToolCallActivityData
  | ToolCallUpdateData
  | ThinkingData
  | PlanData
  | SenderData
  | UsageUpdateData
  | UsageSnapshotData
  | CompactionData
  | ModelFallbackData
  | ModelNoFallbackData
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

export interface TodoItem {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
}

export interface UsageUpdateData {
  type: 'usage_update';
  input_tokens: number;
  output_tokens: number;
}

/**
 * Final usage snapshot emitted before result event.
 * At least one of context_window or input_tokens should be present.
 * All fields are optional to handle incomplete SDK implementations.
 */
export interface UsageSnapshotData {
  type: 'usage_snapshot';
  context_window?: number;
  input_tokens?: number;
  output_tokens?: number;
}

export interface CompactionData {
  type: 'compaction';
  trigger: string;
}

// A model_refusal_fallback system event.
export interface ModelFallbackData {
  type: 'system';
  subtype: 'model_refusal_fallback';
  original_model?: string;
  fallback_model?: string;
  content?: string;
  api_refusal_category?: string | null;
}

// A model_refusal_no_fallback system event.
export interface ModelNoFallbackData {
  type: 'system';
  subtype: 'model_refusal_no_fallback';
  original_model?: string;
  content?: string;
  api_refusal_category?: string | null;
}

export interface NormalizedUsage {
  normalized: 'usage';
  usedTokens: number;
  inputTokens: number;
  outputTokens: number;
  modelContextWindow?: number | null;
}

export interface NormalizedText {
  normalized: 'text';
  text: string;
}

export interface NormalizedError {
  normalized: 'error';
  message: string;
  details?: string;
}

export interface NormalizedFyi {
  normalized: 'fyi';
  title: string;
  details?: string;
}

// Events the normalizer recognises but intentionally hides from the curated
// "All" view (noise — rate limits, mcp startup chatter, echoed user messages,
// thread status pings). Shown under the Debug filter so power users can
// inspect raw JSON when something looks off.
export interface NormalizedDebug {
  normalized: 'debug';
  raw: Record<string, unknown>;
  reason: string;
}

// UsageState — tracked per session in ExecutionDetail
export interface UsageState {
  usedTokens: number;
  inputTokens: number;
  outputTokens: number;
  contextWindow: number;
  compactions: number;
  available: boolean;              // has usage metrics (claude_sdk, codex_sdk)
  supportsContextPercentage: boolean;  // fill bar reliable when a context window is known
}

export interface StateChangePayload {
  desired?: string;
  executor_state?: string;
  outcome?: string;
  desired_by?: string;
  error?: string;
  error_kind?: string;
  stderr?: string;
  from?: string | null;
  to?: string;
}

// Prefill data for re-running or pre-filling execution form
export interface ExecutionPrefill {
  sourceExecutionId?: string;
  projectId?: string | null;
  agentId?: string;
  agentIds?: string[];
  prompt?: string;
  title?: string;
  sandbox_policy?: { fs_level: string };
}

// Response types
export interface CreateExecutionResponse {
  execution: Execution;
  session_id: string;
  warning?: string;
}

export interface ContinueSessionResponse {
  session_id: string;
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
  if ('role' in p) return false;
  // New format: has desired, executor_state, or outcome (but NOT role)
  if ('desired' in p || 'executor_state' in p || 'outcome' in p) return true;
  // Legacy format: has 'to' field
  if ('to' in p) return true;
  return false;
}

export function isEscalateData(d: DataPartPayload): d is EscalateData {
  return d.type === 'escalate';
}

export function isDelegateData(d: DataPartPayload): d is DelegateData {
  return d.type === 'delegate';
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

export function isSenderData(d: DataPartPayload): d is SenderData {
  return d.type === 'sender';
}

export function isUsageUpdateData(d: DataPartPayload): d is UsageUpdateData {
  return d.type === 'usage_update';
}

export function isUsageSnapshotData(d: DataPartPayload): d is UsageSnapshotData {
  return d.type === 'usage_snapshot';
}

export function isCompactionData(d: DataPartPayload): d is CompactionData {
  return d.type === 'compaction';
}

// Type guards keyed on type + subtype.
export function isModelRefusalFallbackData(d: DataPartPayload): d is ModelFallbackData {
  const x = d as { type?: string; subtype?: string };
  return x.type === 'system' && x.subtype === 'model_refusal_fallback';
}

export function isModelRefusalNoFallbackData(
  d: DataPartPayload,
): d is ModelNoFallbackData {
  const x = d as { type?: string; subtype?: string };
  return x.type === 'system' && x.subtype === 'model_refusal_no_fallback';
}

// Returns the model name, or 'Unknown model' when absent or blank.
export function refusalModelName(v: unknown): string {
  return typeof v === 'string' && v.trim() !== '' ? v : 'Unknown model';
}

// Returns a non-empty string, or undefined.
export function refusalDisplayText(v: unknown): string | undefined {
  return typeof v === 'string' && v.trim() !== '' ? v : undefined;
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
  tags: string[];
}

export interface WikiPageListItem {
  slug: string;
  title: string;
  revision_number: number;
  updated_by: string | null;
  updated_at: string;
  score?: number;
  tags: string[];
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
  tags?: string[];
}

export interface WikiTag {
  name: string;
  page_count: number;
}

export interface WikiSubscription {
  id: string;
  project_id: string;
  subscriber: string;
  page_slug?: string | null;
  tag_name?: string | null;
  created_at: string;
}

export interface WikiChange {
  slug: string;
  title: string;
  revision_number: number;
  summary?: string | null;
  created_by?: string | null;
  created_at: string;
}

export interface WikiPageExport {
  slug: string;
  title: string;
  body: string;
}

// MCP Servers
export interface McpServer {
  id: string;
  name: string;
  transport_type: 'stdio' | 'http';
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface McpServerPoolEntry {
  mcp_server_id: string;
  name: string;
  transport_type: string;
  config: Record<string, unknown>;
}

// GET /api/executions/{id}/agents — config pool
export interface AgentPoolEntry {
  agent_id: string;
  name: string;
  description: string | null;
  agent_type: string;
}

// GET /api/executions/{id}/sessions — session discovery
export interface SessionDiscoveryEntry {
  session_id: string;
  hierarchical_name: string;
  agent_name: string;
  role: string;
  status: string;
  parent_name: string | null;
}

// Derived identity info built from SessionDiscoveryEntry
export interface SessionIdentity {
  slug: string;
  hierarchicalName: string;
  agentName: string;
  role: string;
}

// GET/POST /api/config — briefing configuration
export interface ConfigEntry {
  name: string;
  value: string;
  created_at: string;
  updated_at: string;
}

// Driver descriptor types
export interface FieldAnnotation {
  pointer: string;
  label: string;
  help_text: string | null;
  group: string;
  widget: string;
  storage: string;
  support_status: string | { ignored: string };
  secret: boolean;
  suggestions_source: string | null;
}

export interface DriverDescriptor {
  platform: string;
  label: string;
  schema: Record<string, unknown>;
  fields: FieldAnnotation[];
}

export interface ModelSuggestion {
  id: string;
  label: string;
  description?: string;
  recommended?: boolean;
}

// Decision batches from GET /api/decisions
export interface DecisionBatchResponse {
  batch_id: string;
  execution_id: string;
  execution_title: string | null;
  session_id: string;
  agent_name: string;
  hierarchical_name: string;
  status: 'pending' | 'answered' | 'dismissed' | 'expired';
  importance: 'blocking' | 'fyi';
  questions: DecisionQuestionResponse[];
  answer: string | null;
  answered_at: string | null;
  dismissed_at: string | null;
  created_at: string;
}

export interface DecisionQuestionResponse {
  question: string;
  context?: string | null;
  options?: QuestionOption[] | null;
  batch_index: number;
}
