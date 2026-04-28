import type {
  Agent, AgentPoolEntry, SessionDiscoveryEntry, ConfigEntry, Driver, DriverDescriptor, ModelSuggestion,
  Execution, ExecutionDetail, Session, Event, Project,
  CreateExecutionResponse, PostMessageResponse, DiffResponse, WorktreeInfo, BranchesResponse,
  McpServer, McpServerPoolEntry,
  WikiPage, WikiPageListItem, WikiRevision, WikiRevisionListItem, PutWikiPageRequest,
  WikiTag, WikiSubscription, WikiChange, WikiPageExport,
  DecisionBatchResponse,
} from './types';

export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`API ${status}: ${body}`);
    this.name = 'ApiError';
  }
}

export class AgentBeaconAPI {
  private baseURL: string;

  constructor(baseURL: string = '/api') {
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
      const text = await response.text().catch(() => '');
      throw new ApiError(response.status, text || response.statusText);
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
      const text = await response.text().catch(() => '');
      throw new ApiError(response.status, text || response.statusText);
    }
  }

  // Projects
  async getProjects(): Promise<Project[]> {
    return this.fetchJSON<Project[]>('/projects');
  }

  async getProject(id: string): Promise<Project> {
    return this.fetchJSON<Project>(`/projects/${id}`);
  }

  async createProject(req: {
    name: string;
    path: string;
  }): Promise<Project & { warning?: string }> {
    return this.fetchJSON('/projects', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async updateProject(id: string, req: {
    name?: string;
    path?: string;
    settings?: Record<string, unknown>;
  }): Promise<Project> {
    return this.fetchJSON(`/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteProject(id: string): Promise<void> {
    return this.fetchNoContent(`/projects/${id}`, { method: 'DELETE' });
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
    return this.fetchJSON(`/drivers/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteDriver(id: string): Promise<void> {
    return this.fetchNoContent(`/drivers/${id}`, { method: 'DELETE' });
  }

  async getDriverDescriptor(platform: string): Promise<DriverDescriptor> {
    return this.fetchJSON<DriverDescriptor>(`/drivers/${platform}/descriptor`);
  }

  async getDriverModels(platform: string): Promise<ModelSuggestion[]> {
    return this.fetchJSON<ModelSuggestion[]>(`/drivers/${platform}/models`);
  }

  // Agents
  async getAgents(): Promise<Agent[]> {
    return this.fetchJSON<Agent[]>('/agents');
  }

  async getAgent(id: string): Promise<Agent> {
    return this.fetchJSON<Agent>(`/agents/${id}`);
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
    return this.fetchJSON(`/agents/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteAgent(id: string): Promise<void> {
    return this.fetchNoContent(`/agents/${id}`, { method: 'DELETE' });
  }

  async getExecutionAgents(executionId: string): Promise<AgentPoolEntry[]> {
    return this.fetchJSON<AgentPoolEntry[]>(`/executions/${executionId}/agents`);
  }

  async addExecutionAgent(executionId: string, req: { agent_id: string; add_to_project?: boolean }): Promise<void> {
    return this.fetchNoContent(`/executions/${executionId}/agents`, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async removeExecutionAgent(executionId: string, agentId: string): Promise<void> {
    return this.fetchNoContent(`/executions/${executionId}/agents/${agentId}`, {
      method: 'DELETE',
    });
  }

  async getExecutionSessions(executionId: string): Promise<SessionDiscoveryEntry[]> {
    return this.fetchJSON<SessionDiscoveryEntry[]>(`/executions/${executionId}/sessions`);
  }

  async getProjectAgents(projectId: string): Promise<AgentPoolEntry[]> {
    return this.fetchJSON<AgentPoolEntry[]>(`/projects/${projectId}/agents`);
  }

  async addProjectAgent(projectId: string, agentId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${projectId}/agents`, {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId }),
    });
  }

  async removeProjectAgent(projectId: string, agentId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${projectId}/agents/${agentId}`, {
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
    return this.fetchJSON<McpServer>(`/mcp-servers/${id}`);
  }

  async updateMcpServer(id: string, req: {
    name?: string;
    transport_type?: string;
    config?: Record<string, unknown>;
  }): Promise<McpServer> {
    return this.fetchJSON(`/mcp-servers/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteMcpServer(id: string): Promise<void> {
    return this.fetchNoContent(`/mcp-servers/${id}`, { method: 'DELETE' });
  }

  // Project MCP Servers
  async getProjectMcpServers(projectId: string): Promise<McpServerPoolEntry[]> {
    return this.fetchJSON<McpServerPoolEntry[]>(`/projects/${projectId}/mcp-servers`);
  }

  async addProjectMcpServer(projectId: string, mcpServerId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${projectId}/mcp-servers`, {
      method: 'POST',
      body: JSON.stringify({ mcp_server_id: mcpServerId }),
    });
  }

  async removeProjectMcpServer(projectId: string, mcpServerId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${projectId}/mcp-servers/${mcpServerId}`, {
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
    return this.fetchJSON<ExecutionDetail>(`/executions/${id}`);
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
    return this.fetchJSON(`/executions/${id}/terminate`, { method: 'POST' });
  }

  async getExecutionEvents(id: string): Promise<Event[]> {
    return this.fetchJSON<Event[]>(`/executions/${id}/events`);
  }

  // Sessions
  async getSessions(params?: { execution_id?: string }): Promise<Session[]> {
    const search = new URLSearchParams();
    if (params?.execution_id) search.set('execution_id', params.execution_id);
    const qs = search.toString();
    return this.fetchJSON<Session[]>(`/sessions${qs ? `?${qs}` : ''}`);
  }

  async getSessionEvents(sessionId: string): Promise<Event[]> {
    return this.fetchJSON<Event[]>(`/sessions/${sessionId}/events`);
  }

  async terminateSession(sessionId: string): Promise<{ terminated: boolean }> {
    return this.fetchJSON(`/sessions/${sessionId}/terminate`, { method: 'POST' });
  }

  async stopSession(sessionId: string): Promise<{ stopped: boolean; tasks_flushed: number }> {
    return this.fetchJSON(`/sessions/${sessionId}/stop`, { method: 'POST' });
  }

  async continueSession(sessionId: string, parts: import('./types').MessagePart[]): Promise<import('./types').ContinueSessionResponse> {
    return this.fetchJSON(`/sessions/${sessionId}/continue`, {
      method: 'POST',
      body: JSON.stringify({ parts }),
    });
  }

  async recoverSession(sessionId: string, message?: string): Promise<{ session: Session; execution_recovered: boolean }> {
    return this.fetchJSON(`/sessions/${sessionId}/recover`, {
      method: 'POST',
      body: JSON.stringify({ message: message ?? undefined }),
    });
  }

  async getSessionWorktree(sessionId: string): Promise<WorktreeInfo> {
    return this.fetchJSON<WorktreeInfo>(`/sessions/${sessionId}/worktree`);
  }

  async getSessionBranches(sessionId: string): Promise<BranchesResponse> {
    return this.fetchJSON<BranchesResponse>(`/sessions/${sessionId}/worktree/branches`);
  }

  async deleteSessionWorktree(sessionId: string, opts?: { dryRun?: boolean; deleteBranch?: boolean }): Promise<Record<string, unknown>> {
    const search = new URLSearchParams();
    if (opts?.dryRun) search.set('dry_run', 'true');
    if (opts?.deleteBranch) search.set('delete_branch', 'true');
    const qs = search.toString();
    return this.fetchJSON(`/sessions/${sessionId}/worktree${qs ? `?${qs}` : ''}`, { method: 'DELETE' });
  }

  // Session diffs — custom fetch to handle 413 (truncated) as valid data
  async getSessionDiff(sessionId: string, opts?: { base?: string; stat?: boolean }): Promise<DiffResponse> {
    const search = new URLSearchParams();
    if (opts?.base) search.set('base', opts.base);
    if (opts?.stat) search.set('stat', 'true');
    const qs = search.toString();
    const url = `${this.baseURL}/sessions/${sessionId}/worktree/diff${qs ? `?${qs}` : ''}`;
    const response = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
    if (response.status === 413) {
      return response.json();
    }
    if (!response.ok) {
      const text = await response.text().catch(() => '');
      throw new ApiError(response.status, text || response.statusText);
    }
    return response.json();
  }

  async postMessage(
    sessionId: string,
    parts: import('./types').MessagePart[]
  ): Promise<PostMessageResponse> {
    return this.fetchJSON(`/sessions/${sessionId}/message`, {
      method: 'POST',
      body: JSON.stringify({ parts }),
    });
  }

  async checkReady(): Promise<{ status: string }> {
    return this.fetchJSON('/ready');
  }

  // Wiki
  async listWikiPages(projectId: string, q?: string): Promise<WikiPageListItem[]> {
    const params = q ? `?q=${encodeURIComponent(q)}` : '';
    return this.fetchJSON<WikiPageListItem[]>(`/projects/${projectId}/wiki/pages${params}`);
  }

  async getWikiPage(projectId: string, slug: string): Promise<WikiPage> {
    return this.fetchJSON<WikiPage>(`/projects/${projectId}/wiki/pages/${slug}`);
  }

  async putWikiPage(projectId: string, slug: string, req: PutWikiPageRequest): Promise<WikiPage> {
    return this.fetchJSON<WikiPage>(`/projects/${projectId}/wiki/pages/${slug}`, {
      method: 'PUT',
      body: JSON.stringify(req),
    });
  }

  async deleteWikiPage(projectId: string, slug: string): Promise<void> {
    return this.fetchNoContent(`/projects/${projectId}/wiki/pages/${slug}`, {
      method: 'DELETE',
    });
  }

  async listWikiRevisions(projectId: string, slug: string): Promise<WikiRevisionListItem[]> {
    return this.fetchJSON<WikiRevisionListItem[]>(`/projects/${projectId}/wiki/pages/${slug}/revisions`);
  }

  async getWikiRevision(projectId: string, slug: string, rev: number): Promise<WikiRevision> {
    return this.fetchJSON<WikiRevision>(`/projects/${projectId}/wiki/pages/${slug}/revisions/${rev}`);
  }

  async listWikiTags(projectId: string): Promise<WikiTag[]> {
    return this.fetchJSON<WikiTag[]>(`/projects/${projectId}/wiki/tags`);
  }

  async createWikiSubscription(projectId: string, req: {
    subscriber: string;
    page_slug?: string;
    tag_name?: string;
  }): Promise<WikiSubscription> {
    return this.fetchJSON<WikiSubscription>(`/projects/${projectId}/wiki/subscriptions`, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async listWikiSubscriptions(projectId: string, subscriber: string): Promise<WikiSubscription[]> {
    return this.fetchJSON<WikiSubscription[]>(
      `/projects/${projectId}/wiki/subscriptions?subscriber=${encodeURIComponent(subscriber)}`
    );
  }

  async deleteWikiSubscription(projectId: string, subId: string): Promise<void> {
    return this.fetchNoContent(`/projects/${projectId}/wiki/subscriptions/${subId}`, {
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
    return this.fetchJSON<WikiChange[]>(`/projects/${projectId}/wiki/changes${qs ? `?${qs}` : ''}`);
  }

  async exportWiki(projectId: string): Promise<WikiPageExport[]> {
    return this.fetchJSON<WikiPageExport[]>(`/projects/${projectId}/wiki/export`);
  }

  // Decisions
  async getDecisions(params?: {
    execution_id?: string;
    status?: string;
    include_terminal?: boolean;
  }): Promise<{ decisions: DecisionBatchResponse[] }> {
    const search = new URLSearchParams();
    if (params?.execution_id) search.set('execution_id', params.execution_id);
    if (params?.status) search.set('status', params.status);
    if (params?.include_terminal !== undefined) search.set('include_terminal', String(params.include_terminal));
    const qs = search.toString();
    return this.fetchJSON(`/decisions${qs ? `?${qs}` : ''}`);
  }

  async dismissBatch(batchId: string): Promise<{ status: string }> {
    return this.fetchJSON(`/escalate/${batchId}/dismiss`, { method: 'POST' });
  }
}

export const api = new AgentBeaconAPI();
