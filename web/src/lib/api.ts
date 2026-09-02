import type {
  Agent, AgentPoolEntry, SessionDiscoveryEntry, ConfigEntry, Driver, DriverDescriptor, ModelSuggestion,
  Execution, ExecutionDetail, Session, Event, Project,
  CreateExecutionResponse, PostMessageResponse, DiffResponse, WorktreeInfo, BranchesResponse,
  McpServer, McpServerPoolEntry,
  WikiPage, WikiPageListItem, WikiRevision, WikiRevisionListItem, PutWikiPageRequest,
  PatchWikiPageRequest, WikiSearchResult, WikiAccess, ShareTag, ShareTagMember,
  WikiTag, WikiSubscription, WikiChange, WikiPageExport,
  DecisionBatchResponse, DecisionSummaryResponse, Page,
} from './types';

/** Either a fresh pending snapshot or the server's word that nothing changed. */
export type PendingDecisionsResult =
  | { notModified: true }
  | { notModified: false; etag: string | null; body: { decisions: DecisionBatchResponse[] } };

function eventPageQuery(opts?: { before?: string; after?: string; limit?: number }): string {
  const search = new URLSearchParams();
  if (opts?.before) search.set('before', opts.before);
  if (opts?.after) search.set('after', opts.after);
  if (opts?.limit !== undefined) search.set('limit', String(opts.limit));
  const qs = search.toString();
  return qs ? `?${qs}` : '';
}

export interface ProblemDetails {
  title: string;
  status: number;
  code: string;
  detail?: string;
  [key: string]: unknown;
}

export class ApiError extends Error {
  /** Machine-readable code when the server returned application/problem+json. */
  public code?: string;
  /** The parsed problem body, when there was one. */
  public problem?: ProblemDetails;

  constructor(public status: number, public body: string, problem?: ProblemDetails) {
    super(`API ${status}: ${problem?.code ?? body}`);
    this.name = 'ApiError';
    this.problem = problem;
    this.code = problem?.code;
  }
}

/** Build an ApiError, parsing a problem body when there is one. */
async function toApiError(response: Response): Promise<ApiError> {
  const text = await response.text().catch(() => '');
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/problem+json')) {
    try {
      const problem = JSON.parse(text) as ProblemDetails;
      if (problem && typeof problem.code === 'string') {
        return new ApiError(response.status, text, problem);
      }
    } catch {
      // fall through to the opaque form
    }
  }
  return new ApiError(response.status, text || response.statusText);
}

export /** Encode one opaque id for use as a single path segment. */
function pathSegment(value: string | number): string {
  return encodeURIComponent(String(value));
}

class AgentBeaconAPI {
  private baseURL: string;

  constructor(baseURL: string = '/api/v1') {
    this.baseURL = baseURL;
  }

  private async fetchJSON<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;
    const { headers: optHeaders, ...rest } = options;
    const response = await fetch(url, {
      ...rest,
      headers: { 'Content-Type': 'application/json', ...optHeaders },
    });

    if (!response.ok) {
      throw await toApiError(response);
    }

    const contentType = response.headers.get('content-type');
    if (contentType?.includes('application/json')) {
      return response.json();
    }
    return {} as T;
  }

  private async fetchNoContent(endpoint: string, options: RequestInit = {}): Promise<void> {
    const url = `${this.baseURL}${endpoint}`;
    const { headers: optHeaders, ...rest } = options;
    const response = await fetch(url, {
      ...rest,
      headers: { 'Content-Type': 'application/json', ...optHeaders },
    });

    if (!response.ok) {
      throw await toApiError(response);
    }
  }

  // Projects
  async getProjects(): Promise<Project[]> {
    return this.fetchJSON<Project[]>('/projects');
  }

  async getProject(id: string): Promise<Project> {
    return this.fetchJSON<Project>(`/projects/${pathSegment(id)}`);
  }

  async createProject(req: {
    name: string;
    path: string;
    slug?: string;
  }): Promise<Project & { warning?: string }> {
    return this.fetchJSON('/projects', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async updateProject(id: string, req: {
    name?: string;
    path?: string;
    slug?: string;
    settings?: Record<string, unknown>;
  }): Promise<Project> {
    return this.fetchJSON(`/projects/${pathSegment(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteProject(id: string): Promise<void> {
    return this.fetchNoContent(`/projects/${pathSegment(id)}`, { method: 'DELETE' });
  }

  // Drivers
  async getDrivers(): Promise<Driver[]> {
    return this.fetchJSON<Driver[]>('/drivers');
  }

  async createDriver(req: {
    name: string;
    platform: string;
    config?: Record<string, unknown>;
  }): Promise<Driver> {
    return this.fetchJSON('/drivers', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async updateDriver(id: string, req: {
    name?: string;
    config?: Record<string, unknown>;
  }): Promise<Driver> {
    return this.fetchJSON(`/drivers/${pathSegment(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteDriver(id: string): Promise<void> {
    return this.fetchNoContent(`/drivers/${pathSegment(id)}`, { method: 'DELETE' });
  }

  async getDriverDescriptor(platform: string): Promise<DriverDescriptor> {
    return this.fetchJSON<DriverDescriptor>(`/drivers/${pathSegment(platform)}/descriptor`);
  }

  async getDriverModels(platform: string): Promise<ModelSuggestion[]> {
    return this.fetchJSON<ModelSuggestion[]>(`/drivers/${pathSegment(platform)}/models`);
  }

  // Agents
  async getAgents(): Promise<Agent[]> {
    return this.fetchJSON<Agent[]>('/agents');
  }

  async getAgent(id: string): Promise<Agent> {
    return this.fetchJSON<Agent>(`/agents/${pathSegment(id)}`);
  }

  async createAgent(req: {
    name: string;
    description?: string | null;
    driver_id: string;
    config: Record<string, unknown>;
    system_prompt?: string | null;
  }): Promise<Agent> {
    return this.fetchJSON('/agents', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async updateAgent(id: string, req: {
    name?: string;
    description?: string | null;
    config?: Record<string, unknown>;
    enabled?: boolean;
    system_prompt?: string | null;
  }): Promise<Agent> {
    return this.fetchJSON(`/agents/${pathSegment(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteAgent(id: string): Promise<void> {
    return this.fetchNoContent(`/agents/${pathSegment(id)}`, { method: 'DELETE' });
  }

  async getExecutionAgents(executionId: string): Promise<AgentPoolEntry[]> {
    return this.fetchJSON<AgentPoolEntry[]>(`/executions/${pathSegment(executionId)}/agents`);
  }

  async addExecutionAgent(executionId: string, req: { agent_id: string; add_to_project?: boolean }): Promise<void> {
    return this.fetchNoContent(`/executions/${pathSegment(executionId)}/agents`, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async removeExecutionAgent(executionId: string, agentId: string): Promise<void> {
    return this.fetchNoContent(`/executions/${pathSegment(executionId)}/agents/${pathSegment(agentId)}`, {
      method: 'DELETE',
    });
  }

  async getExecutionSessions(executionId: string): Promise<SessionDiscoveryEntry[]> {
    return this.fetchJSON<SessionDiscoveryEntry[]>(`/executions/${pathSegment(executionId)}/sessions`);
  }

  async getProjectAgents(projectId: string): Promise<AgentPoolEntry[]> {
    return this.fetchJSON<AgentPoolEntry[]>(`/projects/${pathSegment(projectId)}/agents`);
  }

  async addProjectAgent(projectId: string, agentId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${pathSegment(projectId)}/agents`, {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async removeProjectAgent(projectId: string, agentId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${pathSegment(projectId)}/agents/${pathSegment(agentId)}`, {
      method: 'DELETE',
    });
  }

  // MCP Servers
  async getMcpServers(): Promise<McpServer[]> {
    return this.fetchJSON<McpServer[]>('/mcp-servers');
  }

  async createMcpServer(req: {
    name: string;
    transport_type: string;
    config: Record<string, unknown>;
  }): Promise<McpServer> {
    return this.fetchJSON('/mcp-servers', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async getMcpServer(id: string): Promise<McpServer> {
    return this.fetchJSON<McpServer>(`/mcp-servers/${pathSegment(id)}`);
  }

  async updateMcpServer(id: string, req: {
    name?: string;
    transport_type?: string;
    config?: Record<string, unknown>;
  }): Promise<McpServer> {
    return this.fetchJSON(`/mcp-servers/${pathSegment(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteMcpServer(id: string): Promise<void> {
    return this.fetchNoContent(`/mcp-servers/${pathSegment(id)}`, { method: 'DELETE' });
  }

  // Project MCP Servers
  async getProjectMcpServers(projectId: string): Promise<McpServerPoolEntry[]> {
    return this.fetchJSON<McpServerPoolEntry[]>(`/projects/${pathSegment(projectId)}/mcp-servers`);
  }

  async addProjectMcpServer(projectId: string, mcpServerId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${pathSegment(projectId)}/mcp-servers`, {
      method: 'POST',
      body: JSON.stringify({ mcp_server_id: mcpServerId }),
    });
  }

  async removeProjectMcpServer(projectId: string, mcpServerId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${pathSegment(projectId)}/mcp-servers/${pathSegment(mcpServerId)}`, {
      method: 'DELETE',
    });
  }

  async getConfig(): Promise<ConfigEntry[]> {
    return this.fetchJSON<ConfigEntry[]>('/config');
  }

  async updateConfig(name: string, value: string): Promise<ConfigEntry> {
    return this.fetchJSON<ConfigEntry>('/config', {
      method: 'POST',
      body: JSON.stringify({ name, value }),
    });
  }

  // Executions
  async getExecutions(params?: {
    limit?: number;
    project_id?: string;
  }): Promise<Execution[]> {
    const search = new URLSearchParams();
    if (params?.limit) search.set('limit', String(params.limit));
    if (params?.project_id) search.set('project_id', params.project_id);
    const qs = search.toString();
    return this.fetchJSON<Execution[]>(`/executions${qs ? `?${qs}` : ''}`);
  }

  async getExecution(id: string): Promise<ExecutionDetail> {
    return this.fetchJSON<ExecutionDetail>(`/executions/${pathSegment(id)}`);
  }

  async createExecution(req: {
    root_agent_id: string;
    agent_ids: string[];
    parts: import('./types').MessagePart[];
    title?: string;
    project_id?: string;
    context_id?: string;
    branch?: string;
    cwd?: string;
    max_depth?: number;
    max_width?: number;
    sandbox_policy?: { fs_level: string };
  }): Promise<CreateExecutionResponse> {
    return this.fetchJSON('/executions', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async terminateExecution(id: string): Promise<{ execution: Execution }> {
    return this.fetchJSON(`/executions/${pathSegment(id)}/terminate`, { method: 'POST' });
  }

  async getExecutionEvent(executionId: string, eventId: string): Promise<Event> {
    return this.fetchJSON<Event>(
      `/executions/${pathSegment(executionId)}/events/${pathSegment(eventId)}`,
    );
  }

  // Sessions
  async getSessions(params?: { execution_id?: string }): Promise<Session[]> {
    const search = new URLSearchParams();
    if (params?.execution_id) search.set('execution_id', params.execution_id);
    const qs = search.toString();
    return this.fetchJSON<Session[]>(`/sessions${qs ? `?${qs}` : ''}`);
  }

  async getSessionEvents(
    sessionId: string,
    opts?: { before?: string; after?: string; limit?: number; signal?: AbortSignal }
  ): Promise<Page<Event>> {
    return this.fetchJSON<Page<Event>>(
      `/sessions/${pathSegment(sessionId)}/events${eventPageQuery(opts)}`,
      { signal: opts?.signal }
    );
  }

  async terminateSession(sessionId: string): Promise<{ terminated: boolean }> {
    return this.fetchJSON(`/sessions/${pathSegment(sessionId)}/terminate`, { method: 'POST' });
  }

  async stopSession(sessionId: string): Promise<{ stopped: boolean; tasks_flushed: number }> {
    return this.fetchJSON(`/sessions/${pathSegment(sessionId)}/stop`, { method: 'POST' });
  }

  async continueSession(sessionId: string, parts: import('./types').MessagePart[]): Promise<import('./types').ContinueSessionResponse> {
    return this.fetchJSON(`/sessions/${pathSegment(sessionId)}/continue`, {
      method: 'POST',
      body: JSON.stringify({ parts }),
    });
  }

  async recoverSession(sessionId: string, message?: string): Promise<{ session: Session; execution_recovered: boolean }> {
    return this.fetchJSON(`/sessions/${pathSegment(sessionId)}/recover`, {
      method: 'POST',
      body: JSON.stringify({ message: message ?? undefined }),
    });
  }

  async getSessionWorktree(sessionId: string): Promise<WorktreeInfo> {
    return this.fetchJSON<WorktreeInfo>(`/sessions/${pathSegment(sessionId)}/worktree`);
  }

  async getSessionBranches(sessionId: string): Promise<BranchesResponse> {
    return this.fetchJSON<BranchesResponse>(`/sessions/${pathSegment(sessionId)}/worktree/branches`);
  }

  async deleteSessionWorktree(sessionId: string, opts?: { dryRun?: boolean; deleteBranch?: boolean }): Promise<Record<string, unknown>> {
    const search = new URLSearchParams();
    if (opts?.dryRun) search.set('dry_run', 'true');
    if (opts?.deleteBranch) search.set('delete_branch', 'true');
    const qs = search.toString();
    return this.fetchJSON(`/sessions/${pathSegment(sessionId)}/worktree${qs ? `?${qs}` : ''}`, { method: 'DELETE' });
  }

  async getSessionDiff(sessionId: string, opts?: { base?: string; stat?: boolean }): Promise<DiffResponse> {
    const search = new URLSearchParams();
    if (opts?.base) search.set('base', opts.base);
    if (opts?.stat) search.set('stat', 'true');
    const qs = search.toString();
    return this.fetchJSON<DiffResponse>(
      `/sessions/${pathSegment(sessionId)}/worktree/diff${qs ? `?${qs}` : ''}`
    );
  }

  async postMessage(
    sessionId: string,
    parts: import('./types').MessagePart[]
  ): Promise<PostMessageResponse> {
    return this.fetchJSON(`/sessions/${pathSegment(sessionId)}/message`, {
      method: 'POST',
      body: JSON.stringify({ parts }),
    });
  }

  // Wiki
  async listWikiPages(projectId: string): Promise<WikiPageListItem[]> {
    return this.fetchJSON<WikiPageListItem[]>(`/projects/${pathSegment(projectId)}/wiki/pages`);
  }

  async getWikiPage(projectId: string, slug: string): Promise<WikiPage> {
    return this.fetchJSON<WikiPage>(`/projects/${pathSegment(projectId)}/wiki/pages/${pathSegment(slug)}`);
  }

  async putWikiPage(projectId: string, slug: string, req: PutWikiPageRequest): Promise<WikiPage> {
    return this.fetchJSON<WikiPage>(`/projects/${pathSegment(projectId)}/wiki/pages/${pathSegment(slug)}`, {
      method: 'PUT',
      body: JSON.stringify(req),
    });
  }

  async patchWikiPage(projectId: string, slug: string, req: PatchWikiPageRequest): Promise<WikiPage> {
    return this.fetchJSON<WikiPage>(`/projects/${pathSegment(projectId)}/wiki/pages/${pathSegment(slug)}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async searchWiki(q: string, opts?: {
    project?: string;
    limit?: number;
    offset?: number;
  }): Promise<WikiSearchResult[]> {
    const search = new URLSearchParams({ q });
    if (opts?.project) search.set('project', opts.project);
    if (opts?.limit != null) search.set('limit', String(opts.limit));
    if (opts?.offset != null) search.set('offset', String(opts.offset));
    return this.fetchJSON<WikiSearchResult[]>(`/wiki/search?${search.toString()}`);
  }

  // Share tags — operator-level administration, not scoped to a project
  async listShareTags(): Promise<ShareTag[]> {
    return this.fetchJSON<ShareTag[]>('/wiki/tags');
  }

  async addShareTagMember(tagId: string, req: {
    project: string;
    access_level: WikiAccess;
    acknowledge_share?: boolean;
  }): Promise<ShareTagMember & { id: string; tag_id: string; created_at: string }> {
    return this.fetchJSON(`/wiki/tags/${pathSegment(tagId)}/members`, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async updateShareTagMember(tagId: string, project: string, req: {
    access_level: WikiAccess;
    acknowledge_share?: boolean;
  }): Promise<ShareTagMember & { id: string; tag_id: string; created_at: string }> {
    return this.fetchJSON(`/wiki/tags/${pathSegment(tagId)}/members/${pathSegment(project)}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async removeShareTagMember(tagId: string, project: string): Promise<void> {
    return this.fetchNoContent(`/wiki/tags/${pathSegment(tagId)}/members/${pathSegment(project)}`, {
      method: 'DELETE',
    });
  }

  async deleteWikiPage(projectId: string, slug: string): Promise<void> {
    return this.fetchNoContent(`/projects/${pathSegment(projectId)}/wiki/pages/${pathSegment(slug)}`, {
      method: 'DELETE',
    });
  }

  async listWikiRevisions(projectId: string, slug: string): Promise<WikiRevisionListItem[]> {
    return this.fetchJSON<WikiRevisionListItem[]>(`/projects/${pathSegment(projectId)}/wiki/pages/${pathSegment(slug)}/revisions`);
  }

  async getWikiRevision(projectId: string, slug: string, rev: number): Promise<WikiRevision> {
    return this.fetchJSON<WikiRevision>(`/projects/${pathSegment(projectId)}/wiki/pages/${pathSegment(slug)}/revisions/${pathSegment(rev)}`);
  }

  async listWikiTags(projectId: string): Promise<WikiTag[]> {
    return this.fetchJSON<WikiTag[]>(`/projects/${pathSegment(projectId)}/wiki/tags`);
  }

  async createWikiSubscription(projectId: string, req: {
    subscriber: string;
    page_slug?: string;
    tag_name?: string;
  }): Promise<WikiSubscription> {
    return this.fetchJSON<WikiSubscription>(`/projects/${pathSegment(projectId)}/wiki/subscriptions`, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async listWikiSubscriptions(projectId: string, subscriber: string): Promise<WikiSubscription[]> {
    return this.fetchJSON<WikiSubscription[]>(
      `/projects/${pathSegment(projectId)}/wiki/subscriptions?subscriber=${pathSegment(subscriber)}`
    );
  }

  async deleteWikiSubscription(projectId: string, subId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${pathSegment(projectId)}/wiki/subscriptions/${pathSegment(subId)}`, {
      method: 'DELETE',
    });
  }

  async getWikiChanges(projectId: string, params?: {
    since?: string;
    execution_id?: string;
    limit?: number;
  }): Promise<WikiChange[]> {
    const search = new URLSearchParams();
    if (params?.since) search.set('since', params.since);
    if (params?.execution_id) search.set('execution_id', params.execution_id);
    if (params?.limit) search.set('limit', String(params.limit));
    const qs = search.toString();
    return this.fetchJSON<WikiChange[]>(`/projects/${pathSegment(projectId)}/wiki/changes${qs ? `?${qs}` : ''}`);
  }

  async exportWiki(projectId: string): Promise<WikiPageExport[]> {
    return this.fetchJSON<WikiPageExport[]>(`/projects/${pathSegment(projectId)}/wiki/export`);
  }

  // Decisions

  /**
   * Pending decisions. Sends `If-None-Match` when given an ETag and reports a
   * 304 as `notModified` instead of throwing.
   */
  async getDecisionsPending(
    params?: { execution_id?: string; etag?: string | null }
  ): Promise<PendingDecisionsResult> {
    const search = new URLSearchParams();
    if (params?.execution_id) search.set('execution_id', params.execution_id);
    const qs = search.toString();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (params?.etag) headers['If-None-Match'] = params.etag;
    const response = await fetch(`${this.baseURL}/decisions${qs ? `?${qs}` : ''}`, { headers });
    if (response.status === 304) {
      return { notModified: true };
    }
    if (!response.ok) {
      throw await toApiError(response);
    }
    return {
      notModified: false,
      etag: response.headers.get('etag'),
      body: await response.json(),
    };
  }

  async getDecisionsResolved(
    params?: { before?: string; limit?: number }
  ): Promise<Page<DecisionSummaryResponse>> {
    const search = new URLSearchParams();
    search.set('state', 'resolved');
    if (params?.before) search.set('before', params.before);
    if (params?.limit !== undefined) search.set('limit', String(params.limit));
    return this.fetchJSON(`/decisions?${search.toString()}`);
  }

  async getDecision(eventId: string): Promise<DecisionBatchResponse> {
    return this.fetchJSON(`/decisions/${pathSegment(eventId)}`);
  }

  async dismissDecision(eventId: string): Promise<{ status: string }> {
    return this.fetchJSON(`/escalate/${pathSegment(eventId)}/dismiss`, {
      method: 'POST',
    });
  }
}

export const api = new AgentBeaconAPI();
