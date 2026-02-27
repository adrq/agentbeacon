import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api } from '../api';

export function agentsQuery() {
  return createQuery(() => ({
    queryKey: ['agents'],
    queryFn: () => api.getAgents(),
  }));
}

export function driversQuery() {
  return createQuery(() => ({
    queryKey: ['drivers'],
    queryFn: () => api.getDrivers(),
  }));
}

export function createDriverMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (req: {
      name: string;
      platform: string;
      config?: Record<string, unknown>;
    }) => api.createDriver(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['drivers'] });
    },
  }));
}

export function deleteDriverMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (id: string) => api.deleteDriver(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['drivers'] });
    },
  }));
}

export function createAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (req: {
      name: string;
      description?: string | null;
      driver_id: string;
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
