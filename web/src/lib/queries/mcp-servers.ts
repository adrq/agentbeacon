import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api } from '../api';

export function mcpServersQuery() {
  return createQuery(() => ({
    queryKey: ['mcp-servers'],
    queryFn: () => api.getMcpServers(),
  }));
}

export function createMcpServerMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (req: { name: string; transport_type: string; config: Record<string, unknown> }) =>
      api.createMcpServer(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] });
    },
  }));
}

export function updateMcpServerMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: {
      id: string;
      req: { name?: string; transport_type?: string; config?: Record<string, unknown> };
    }) => api.updateMcpServer(args.id, args.req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] });
    },
  }));
}

export function deleteMcpServerMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (id: string) => api.deleteMcpServer(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['mcp-servers'] });
    },
  }));
}

export function projectMcpServersQuery(projectId: () => string | null) {
  return createQuery(() => ({
    queryKey: ['project-mcp-servers', projectId()],
    queryFn: () => api.getProjectMcpServers(projectId()!),
    enabled: !!projectId(),
  }));
}

export function addProjectMcpServerMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; mcpServerId: string }) =>
      api.addProjectMcpServer(args.projectId, args.mcpServerId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['project-mcp-servers', variables.projectId] });
    },
  }));
}

export function removeProjectMcpServerMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; mcpServerId: string }) =>
      api.removeProjectMcpServer(args.projectId, args.mcpServerId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['project-mcp-servers', variables.projectId] });
    },
  }));
}
