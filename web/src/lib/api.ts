// TypeScript interfaces for API data models
export interface Workflow {
  id: string;
  name: string;
  yaml_source: string;
  parsed_json?: any;
  config_refs?: any;
  created_at: string;
  updated_at: string;
}

export interface Execution {
  id: string;
  workflow_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  node_states?: any;
  started_at: string;
  completed_at?: string;
}

export interface Config {
  id: string;
  name: string;
  api_keys?: any;
  agent_settings?: any;
  created_at: string;
  updated_at: string;
}

export interface CreateWorkflowRequest {
  name: string;
  yaml_source: string;
}

export interface UpdateWorkflowRequest {
  name?: string;
  yaml_source?: string;
}

// API Client class
export class AgentMaestroAPI {
  private baseURL: string;

  constructor(baseURL: string = '/api') {
    this.baseURL = baseURL;
  }

  // Generic fetch wrapper with error handling
  private async fetchJSON<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseURL}${endpoint}`;
    const config = {
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
      ...options,
    };

    try {
      const response = await fetch(url, config);

      if (!response.ok) {
        throw new Error(`API request failed: ${response.status} ${response.statusText}`);
      }

      // Handle empty responses
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        return await response.json();
      } else {
        return {} as T;
      }
    } catch (error) {
      console.error('API request error:', error);
      throw error;
    }
  }

  // Workflow operations
  async getWorkflows(): Promise<Workflow[]> {
    return this.fetchJSON<Workflow[]>('/workflows');
  }

  async getWorkflow(id: string): Promise<Workflow> {
    return this.fetchJSON<Workflow>(`/workflows/${id}`);
  }

  async createWorkflow(workflow: CreateWorkflowRequest): Promise<Workflow> {
    return this.fetchJSON<Workflow>('/workflows', {
      method: 'POST',
      body: JSON.stringify(workflow),
    });
  }

  async updateWorkflow(id: string, workflow: UpdateWorkflowRequest): Promise<Workflow> {
    return this.fetchJSON<Workflow>(`/workflows/${id}`, {
      method: 'PUT',
      body: JSON.stringify(workflow),
    });
  }

  async deleteWorkflow(id: string): Promise<void> {
    return this.fetchJSON<void>(`/workflows/${id}`, {
      method: 'DELETE',
    });
  }

  // Execution operations
  async getExecutions(workflowId?: string): Promise<Execution[]> {
    const endpoint = workflowId ? `/executions?workflow_id=${workflowId}` : '/executions';
    return this.fetchJSON<Execution[]>(endpoint);
  }

  async getExecution(id: string): Promise<Execution> {
    return this.fetchJSON<Execution>(`/executions/${id}`);
  }

  async startExecution(workflowId: string): Promise<Execution> {
    return this.fetchJSON<Execution>('/executions', {
      method: 'POST',
      body: JSON.stringify({ workflow_id: workflowId }),
    });
  }

  async stopExecution(id: string): Promise<void> {
    return this.fetchJSON<void>(`/executions/${id}/stop`, {
      method: 'POST',
    });
  }

  // Configuration operations
  async getConfigs(): Promise<Config[]> {
    return this.fetchJSON<Config[]>('/configs');
  }

  async getConfig(id: string): Promise<Config> {
    return this.fetchJSON<Config>(`/configs/${id}`);
  }

  async createConfig(config: Omit<Config, 'id' | 'created_at' | 'updated_at'>): Promise<Config> {
    return this.fetchJSON<Config>('/configs', {
      method: 'POST',
      body: JSON.stringify(config),
    });
  }

  async updateConfig(id: string, config: Partial<Config>): Promise<Config> {
    return this.fetchJSON<Config>(`/configs/${id}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    });
  }

  async deleteConfig(id: string): Promise<void> {
    return this.fetchJSON<void>(`/configs/${id}`, {
      method: 'DELETE',
    });
  }
}

// Export a default API client instance
export const api = new AgentMaestroAPI();
