import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import type { CreateExecutionResponse, ExecutionDetail } from '../types';
import { api } from '../api';

const terminalStatuses = new Set(['completed', 'failed', 'canceled']);

export function executionsQuery(projectId?: () => string | null | undefined) {
  return createQuery(() => ({
    queryKey: ['executions', { projectId: projectId?.() ?? undefined }],
    queryFn: () => api.getExecutions({
      project_id: projectId?.() ?? undefined,
    }),
    refetchInterval: 5000,
    gcTime: 10_000,
  }));
}

export function executionDetailQuery(id: () => string | null) {
  return createQuery(() => ({
    queryKey: ['execution', id()],
    queryFn: () => api.getExecution(id()!),
    enabled: !!id(),
    refetchInterval: (query) => {
      const status = (query.state.data as ExecutionDetail | undefined)?.execution?.status;
      if (status && terminalStatuses.has(status)) return false;
      return 10_000; // SSE delivers events in real-time; poll is a safety net
    },
  }));
}

export function sessionEventsQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
  sseActive?: () => boolean,
) {
  return createQuery(() => ({
    queryKey: ['session-events', sessionId()],
    queryFn: () => api.getSessionEvents(sessionId()!),
    enabled: !!sessionId(),
    refetchInterval: () => {
      if (isTerminal?.()) return false;
      if (sseActive?.()) return false;
      return 3000;
    },
  }));
}

export function executionEventsQuery(executionId: () => string | null | undefined) {
  return createQuery(() => ({
    queryKey: ['execution-events', executionId()],
    queryFn: () => api.getExecutionEvents(executionId()!),
    enabled: !!executionId(),
    refetchInterval: 3000,
  }));
}

export function sessionDiffQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
) {
  return createQuery(() => ({
    queryKey: ['session-diff', sessionId()],
    queryFn: () => api.getSessionDiff(sessionId()!),
    enabled: !!sessionId(),
    staleTime: 5_000,
    refetchInterval: () => {
      if (isTerminal?.()) return false;
      return 10_000;
    },
    retry: (failureCount: number, error: Error) => {
      if (error.message.startsWith('API 4')) return false;
      return failureCount < 2;
    },
  }));
}

export function inputRequiredSessionsQuery() {
  return createQuery(() => ({
    queryKey: ['sessions', { status: 'input-required' }],
    queryFn: () => api.getSessions({ status: 'input-required' }),
    refetchInterval: 5000,
  }));
}

export function executionAgentsQuery(executionId: () => string | null) {
  return createQuery(() => ({
    queryKey: ['execution-agents', executionId()],
    queryFn: () => api.getExecutionAgents(executionId()!),
    enabled: !!executionId(),
  }));
}

export function createExecutionMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (req: {
      root_agent_id: string;
      agent_ids: string[];
      prompt: string;
      title?: string;
      project_id?: string;
      context_id?: string;
      branch?: string;
      cwd?: string;
      max_depth?: number;
      max_width?: number;
    }) => api.createExecution(req),
    onSuccess: (_data: CreateExecutionResponse) => {
      queryClient.invalidateQueries({ queryKey: ['executions'] });
    },
  }));
}

export function cancelExecutionMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (id: string) => api.cancelExecution(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executions'] });
      queryClient.invalidateQueries({ queryKey: ['execution'] });
    },
  }));
}

export function completeExecutionMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (id: string) => api.completeExecution(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executions'] });
      queryClient.invalidateQueries({ queryKey: ['execution'] });
    },
  }));
}
