import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api } from '../api';

export function projectsQuery() {
  return createQuery(() => ({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
  }));
}

export function projectDetailQuery(id: () => string | null) {
  return createQuery(() => ({
    queryKey: ['project', id()],
    queryFn: () => api.getProject(id()!),
    enabled: !!id(),
  }));
}

export function createProjectMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (req: { name: string; path: string }) =>
      api.createProject(req),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
    },
  }));
}

export function updateProjectMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: {
      id: string;
      req: { name?: string; path?: string; settings?: Record<string, unknown> };
    }) => api.updateProject(args.id, args.req),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      queryClient.invalidateQueries({ queryKey: ['project', variables.id] });
    },
  }));
}

export function projectAgentsQuery(projectId: () => string | null) {
  return createQuery(() => ({
    queryKey: ['project-agents', projectId()],
    queryFn: () => api.getProjectAgents(projectId()!),
    enabled: !!projectId(),
  }));
}

export function addProjectAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; agentId: string }) =>
      api.addProjectAgent(args.projectId, args.agentId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['project-agents', variables.projectId] });
    },
  }));
}

export function removeProjectAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; agentId: string }) =>
      api.removeProjectAgent(args.projectId, args.agentId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['project-agents', variables.projectId] });
    },
  }));
}

export function deleteProjectMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (id: string) => api.deleteProject(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
    },
  }));
}
