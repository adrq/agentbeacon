import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import type { CreateExecutionResponse } from '../types';
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
      const status = (query.state.data as { execution?: { status?: string } } | undefined)
        ?.execution?.status;
      if (status && terminalStatuses.has(status)) return false;
      return 3000;
    },
  }));
}

export function sessionEventsQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
) {
  return createQuery(() => ({
    queryKey: ['session-events', sessionId()],
    queryFn: () => api.getSessionEvents(sessionId()!),
    enabled: !!sessionId(),
    refetchInterval: isTerminal?.() ? false : 3000,
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

export function inputRequiredSessionsQuery() {
  return createQuery(() => ({
    queryKey: ['sessions', { status: 'input-required' }],
    queryFn: () => api.getSessions({ status: 'input-required' }),
    refetchInterval: 5000,
  }));
}

export function createExecutionMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (req: {
      agent_id: string;
      prompt: string;
      title?: string;
      project_id?: string;
      context_id?: string;
      branch?: string;
      cwd?: string;
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
