import type {
  Agent, AgentType, Execution, ExecutionDetail, Session, Event, Project,
  CreateExecutionResponse, PostMessageResponse,
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

  // Agents
  async getAgents(): Promise<Agent[]> {
    return this.fetchJSON<Agent[]>('/agents');
  }

  async createAgent(req: {
    name: string;
    description?: string | null;
    agent_type: AgentType;
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
  }): Promise<CreateExecutionResponse> {
    return this.fetchJSON('/executions', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async cancelExecution(id: string): Promise<{ execution: Execution }> {
    return this.fetchJSON(`/executions/${id}/cancel`, { method: 'POST' });
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
}

export const api = new AgentBeaconAPI();
