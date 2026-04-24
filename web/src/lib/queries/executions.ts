import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import type { CreateExecutionResponse, ExecutionDetail } from '../types';
import { api } from '../api';

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
      const data = query.state.data as ExecutionDetail | undefined;
      if (!data) return 10_000;
      // Keep polling until tree is fully settled (all sessions have outcome)
      const allSettled = data.execution.outcome != null
        && data.sessions.every(s => s.outcome != null);
      if (allSettled) return false;
      return 10_000;
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

export function sessionBranchesQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
) {
  return createQuery(() => ({
    queryKey: ['session-branches', sessionId()],
    queryFn: () => api.getSessionBranches(sessionId()!),
    enabled: !!sessionId(),
    staleTime: 30_000,
    refetchInterval: () => {
      if (isTerminal?.()) return false;
      return 30_000;
    },
    retry: (failureCount: number, error: Error) => {
      if (error.message.startsWith('API 4')) return false;
      return failureCount < 2;
    },
  }));
}

export function sessionDiffQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
  base?: () => string | undefined,
) {
  return createQuery(() => ({
    queryKey: ['session-diff', sessionId(), base?.()],
    queryFn: () => api.getSessionDiff(sessionId()!, { base: base?.() }),
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

/**
 */
export function inputCapableSessionsQuery() {
  return createQuery(() => ({
    queryKey: ['sessions', { filter: 'input-capable' }],
    queryFn: async () => {
      const all = await api.getSessions();
      return all.filter(s => !s.parent_session_id && !s.outcome && s.desired !== 'terminate');
    },
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
      parts: import('../types').MessagePart[];
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

export function terminateExecutionMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (id: string) => api.terminateExecution(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executions'] });
      queryClient.invalidateQueries({ queryKey: ['execution'] });
    },
  }));
}

export function recoverSessionMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (params: { sessionId: string; message?: string }) =>
      api.recoverSession(params.sessionId, params.message),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['executions'] });
      queryClient.invalidateQueries({ queryKey: ['execution'] });
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    },
  }));
}

export function executionSessionsQuery(executionId: () => string | null) {
  return createQuery(() => ({
    queryKey: ['execution-sessions', executionId()],
    queryFn: () => api.getExecutionSessions(executionId()!),
    enabled: !!executionId(),
    staleTime: 5_000,
    refetchInterval: 10_000,
  }));
}

export function buildSessionIdentityMap(entries: import('../types').SessionDiscoveryEntry[]): Map<string, import('../types').SessionIdentity> {
  const map = new Map<string, import('../types').SessionIdentity>();
  for (const e of entries) {
    const slug = e.hierarchical_name.split('/').pop() ?? e.session_id.slice(0, 8);
    map.set(e.session_id, {
      slug,
      hierarchicalName: e.hierarchical_name,
      agentName: e.agent_name,
      role: e.role,
    });
  }
  return map;
}
