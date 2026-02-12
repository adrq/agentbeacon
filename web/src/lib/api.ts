import type { Agent, Execution, ExecutionDetail, Session, Event } from './types';

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

  async getAgents(): Promise<Agent[]> {
    return this.fetchJSON<Agent[]>('/agents');
  }

  async getExecutions(params?: { status?: string; limit?: number }): Promise<Execution[]> {
    const search = new URLSearchParams();
    if (params?.status) search.set('status', params.status);
    if (params?.limit) search.set('limit', String(params.limit));
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
  }): Promise<{ execution_id: string; session_id: string; status: string }> {
    return this.fetchJSON('/executions', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

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
  ): Promise<{ event_id: number; status: string }> {
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
