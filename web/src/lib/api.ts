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
  status: 'pending' | 'running' | 'completed' | 'failed' | 'canceled';
  task_states: any;
  created_at: string;
  updated_at: string;
  completed_at?: string;
}

export interface ExecutionEvent {
  id: number;
  execution_id: string;
  event_type: string;
  task_id?: string;
  message: string;
  metadata: any;
  timestamp: string;
}

export interface ExecutionDetail {
  id: string;
  workflow_id: string;
  workflow_definition: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'canceled';
  task_states: any;
  created_at: string;
  updated_at: string;
  completed_at?: string;
  started_at?: string;
  version?: string;
  agent_name?: string;
  events: ExecutionEvent[];
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

// Backend response types (new API contract)
interface ValidationSuccessResponse {
  status: 'ok';
}

interface ValidationErrorResponse {
  status: 'error';
  issues: string[];
}

// Frontend internal type (for ErrorPanel compatibility)
export interface ValidationError {
  type: 'syntax' | 'structural' | 'semantic';
  message: string;
  line?: number;
  node?: string;
  nodes?: string[];
}

// Adapted response for frontend consumers
export interface ValidationResponse {
  valid: boolean;
  errors: ValidationError[];
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

  async validateWorkflow(yaml: string): Promise<ValidationResponse> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000); // 30 second timeout

    try {
      const response = await fetch(`${this.baseURL}/workflows/validate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ yaml }),
        signal: controller.signal,
      });

      clearTimeout(timeout);

      // HTTP 200 = validation success
      if (response.status === 200) {
        const data: ValidationSuccessResponse = await response.json();
        if (data.status === 'ok') {
          return { valid: true, errors: [] };
        }
        // Unexpected response format
        throw new Error('Unexpected success response format');
      }

      // HTTP 422 = validation failure (DAG guardrails)
      if (response.status === 422) {
        const data: ValidationErrorResponse = await response.json();
        return {
          valid: false,
          errors: data.issues.map(issue => ({
            type: 'semantic' as const,
            message: issue,
          })),
        };
      }

      // HTTP 400 or other errors - extract error message from response body
      try {
        const errorData = await response.json();
        const errorMessage = errorData.error || errorData.message || `Validation failed: ${response.status} ${response.statusText}`;
        return {
          valid: false,
          errors: [
            {
              type: 'syntax',
              message: errorMessage,
            },
          ],
        };
      } catch {
        // If response body is not JSON, fall back to generic error
        return {
          valid: false,
          errors: [
            {
              type: 'syntax',
              message: `Validation failed: ${response.status} ${response.statusText}`,
            },
          ],
        };
      }

    } catch (error) {
      clearTimeout(timeout);

      // Handle timeout specifically
      if (error instanceof Error && error.name === 'AbortError') {
        return {
          valid: false,
          errors: [
            {
              type: 'syntax',
              message: 'Validation timeout - please try again',
            },
          ],
        };
      }

      // Network/other errors
      throw error;
    }
  }

  // Execution operations
  async getExecutions(workflowId?: string): Promise<Execution[]> {
    const endpoint = workflowId ? `/executions?workflow_id=${workflowId}` : '/executions';
    return this.fetchJSON<Execution[]>(endpoint);
  }

  async getExecution(id: string): Promise<Execution> {
    return this.fetchJSON<Execution>(`/executions/${id}`);
  }

  async getExecutionDetail(id: string): Promise<ExecutionDetail> {
    return this.fetchJSON<ExecutionDetail>(`/executions/${id}`);
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

  // Health check operations
  async checkReady(): Promise<{ status: string; ok: boolean }> {
    try {
      const response = await fetch(`${this.baseURL}/ready`);
      const data = await response.json();
      return { status: data.status, ok: response.ok };
    } catch (error) {
      return { status: 'error', ok: false };
    }
  }

  // Agent card operations
  async fetchAgentCard(): Promise<{ url: string }> {
    // Agent card is at root level, not under /api
    const response = await fetch('/.well-known/agent-card.json');
    if (!response.ok) {
      throw new Error(`Failed to fetch agent card: ${response.status} ${response.statusText}`);
    }
    return await response.json();
  }

  // Workflow execution trigger via A2A protocol
  async triggerWorkflowExecution(workflowYaml: string): Promise<string> {
    // Use proxied /rpc endpoint instead of agent-card URL to avoid CORS issues in dev
    const response = await fetch('/rpc', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        method: 'message/send',
        params: {
          message: {
            role: 'user',
            messageId: `ui-${Date.now()}`,
            kind: 'message',
            parts: [
              {
                kind: 'data',
                data: {
                  workflowYaml: workflowYaml,
                },
              },
            ],
          },
        },
        id: Date.now(),
      }),
    });

    if (!response.ok) {
      throw new Error(`Failed to trigger workflow: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();

    if (data.error) {
      // Extract detailed error message from nested error data if available
      // Try multiple nested paths: data.error, data.detail, or message
      const detailedError =
        data.error.data?.error ||
        data.error.data?.detail ||
        data.error.message ||
        'Workflow execution failed';
      throw new Error(detailedError);
    }

    if (!data.result || !data.result.id) {
      throw new Error('Invalid response: missing execution id');
    }

    return data.result.id;
  }
}

// Export a default API client instance
export const api = new AgentMaestroAPI();
