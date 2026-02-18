import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import type { AgentType } from '../types';
import { api } from '../api';

export function agentsQuery() {
  return createQuery(() => ({
    queryKey: ['agents'],
    queryFn: () => api.getAgents(),
  }));
}

export function createAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (req: {
      name: string;
      description?: string | null;
      agent_type: AgentType;
      config: Record<string, unknown>;
      sandbox_config?: Record<string, unknown> | null;
    }) => api.createAgent(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  }));
}

export function updateAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: {
      id: string;
      req: {
        name?: string;
        description?: string | null;
        config?: Record<string, unknown>;
        sandbox_config?: Record<string, unknown> | null;
        enabled?: boolean;
      };
    }) => api.updateAgent(args.id, args.req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  }));
}

export function deleteAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (id: string) => api.deleteAgent(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['agents'] });
    },
  }));
}
