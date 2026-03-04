import type {
  Agent, AgentDiscoveryEntry, Driver, Execution, ExecutionDetail, Session, Event, Project,
  CreateExecutionResponse, PostMessageResponse,
  WikiPage, WikiPageListItem, WikiRevision, WikiRevisionListItem, PutWikiPageRequest,
  WikiTag, WikiSubscription, WikiChange, WikiPageExport,
} from './types';

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
      throw new Error(`API ${response.status}: ${text || response.statusText}`);
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
      throw new Error(`API ${response.status}: ${text || response.statusText}`);
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
    default_agent_id?: string | null;
  }): Promise<Project & { warning?: string }> {
    return this.fetchJSON('/projects', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async updateProject(id: string, req: {
    name?: string;
    path?: string;
    default_agent_id?: string | null;
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
    sandbox_config?: Record<string, unknown> | null;
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
    sandbox_config?: Record<string, unknown> | null;
    enabled?: boolean;
  }): Promise<Agent> {
    return this.fetchJSON(`/agents/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(req),
    });
  }

  async deleteAgent(id: string): Promise<void> {
    return this.fetchNoContent(`/agents/${id}`, { method: 'DELETE' });
  }

  async getExecutionAgents(executionId: string): Promise<AgentDiscoveryEntry[]> {
    return this.fetchJSON<AgentDiscoveryEntry[]>(`/executions/${executionId}/agents`);
  }

  // Executions
  async getExecutions(params?: {
    status?: string;
    limit?: number;
    project_id?: string;
  }): Promise<Execution[]> {
    const search = new URLSearchParams();
    if (params?.status) search.set('status', params.status);
    if (params?.limit) search.set('limit', String(params.limit));
    if (params?.project_id) search.set('project_id', params.project_id);
    const qs = search.toString();
    return this.fetchJSON<Execution[]>(`/executions${qs ? `?${qs}` : ''}`);
  }

  async getExecution(id: string): Promise<ExecutionDetail> {
    return this.fetchJSON<ExecutionDetail>(`/executions/${id}`);
  }

  async createExecution(req: {
    agent_id: string;
    prompt: string;
    title?: string;
    project_id?: string;
    context_id?: string;
    branch?: string;
    cwd?: string;
    max_depth?: number;
    max_width?: number;
  }): Promise<CreateExecutionResponse> {
    return this.fetchJSON('/executions', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async cancelExecution(id: string): Promise<{ execution: Execution }> {
    return this.fetchJSON(`/executions/${id}/cancel`, { method: 'POST' });
  }

  async completeExecution(id: string): Promise<{ execution: Execution }> {
    return this.fetchJSON(`/executions/${id}/complete`, { method: 'POST' });
  }

  async getExecutionEvents(id: string): Promise<Event[]> {
    return this.fetchJSON<Event[]>(`/executions/${id}/events`);
  }

  // Sessions
  async getSessions(params?: { status?: string; execution_id?: string }): Promise<Session[]> {
    const search = new URLSearchParams();
    if (params?.status) search.set('status', params.status);
    if (params?.execution_id) search.set('execution_id', params.execution_id);
    const qs = search.toString();
    return this.fetchJSON<Session[]>(`/sessions${qs ? `?${qs}` : ''}`);
  }

  async getSessionEvents(sessionId: string): Promise<Event[]> {
    return this.fetchJSON<Event[]>(`/sessions/${sessionId}/events`);
  }

  async cancelSession(sessionId: string): Promise<{ canceled: boolean; sessions_terminated: number }> {
    return this.fetchJSON(`/sessions/${sessionId}/cancel`, { method: 'POST' });
  }

  async completeSession(sessionId: string): Promise<{ completed: boolean; sessions_terminated: number }> {
    return this.fetchJSON(`/sessions/${sessionId}/complete`, { method: 'POST' });
  }

  async postMessage(
    sessionId: string,
    message: string
  ): Promise<PostMessageResponse> {
    return this.fetchJSON(`/sessions/${sessionId}/message`, {
      method: 'POST',
      body: JSON.stringify({ message }),
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
}

export const api = new AgentBeaconAPI();
