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
    mutationFn: (req: { name: string; path: string; default_agent_id?: string | null }) =>
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
      req: { name?: string; path?: string; default_agent_id?: string | null; settings?: Record<string, unknown> };
    }) => api.updateProject(args.id, args.req),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      queryClient.invalidateQueries({ queryKey: ['project', variables.id] });
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
