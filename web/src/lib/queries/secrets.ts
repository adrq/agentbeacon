// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api, ApiError } from '../api';

/** The problem detail of a failed request, else its message. */
export function secretErrorText(err: unknown): string {
  if (err instanceof ApiError && typeof err.problem?.detail === 'string') return err.problem.detail;
  return err instanceof Error ? err.message : 'Request failed';
}

export function projectSecretsQuery(projectId: () => string | null) {
  return createQuery(() => ({
    queryKey: ['project-secrets', projectId()],
    queryFn: ({ queryKey }) => api.getProjectSecrets(queryKey[1] as string),
    enabled: !!projectId(),
  }));
}

export function executionSecretsQuery(executionId: () => string | null) {
  return createQuery(() => ({
    queryKey: ['execution-secrets', executionId()],
    queryFn: ({ queryKey }) => api.getExecutionSecrets(queryKey[1] as string),
    enabled: !!executionId(),
  }));
}

export function createProjectSecretMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; name: string; value: string }) =>
      api.createProjectSecret(args.projectId, { name: args.name, value: args.value }),
    onSettled: (_data, _err, variables) =>
      queryClient.invalidateQueries({ queryKey: ['project-secrets', variables.projectId] }),
  }));
}

export function rotateProjectSecretMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; name: string; value: string }) =>
      api.rotateProjectSecret(args.projectId, args.name, { value: args.value }),
    onSettled: (_data, _err, variables) =>
      queryClient.invalidateQueries({ queryKey: ['project-secrets', variables.projectId] }),
  }));
}

export function deleteProjectSecretMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { projectId: string; name: string }) =>
      api.deleteProjectSecret(args.projectId, args.name),
    onSettled: (_data, _err, variables) =>
      queryClient.invalidateQueries({ queryKey: ['project-secrets', variables.projectId] }),
  }));
}

export function attachExecutionSecretMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { executionId: string; projectId: string; name: string }) =>
      api.attachExecutionSecret(args.executionId, args.name),
    onSettled: (_data, _err, variables) => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['execution-secrets', variables.executionId] }),
      queryClient.invalidateQueries({ queryKey: ['project-secrets', variables.projectId] }),
    ]),
  }));
}

/** Create a project secret, then attach it. Resolves with the attach error, if any. */
export function createAndAttachSecretMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: async (args: { executionId: string; projectId: string; name: string; value: string }) => {
      await api.createProjectSecret(args.projectId, { name: args.name, value: args.value });
      try {
        await api.attachExecutionSecret(args.executionId, args.name);
        return { attachError: null as unknown };
      } catch (err) {
        return { attachError: err };
      }
    },
    onSettled: (_data, _err, variables) => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['execution-secrets', variables.executionId] }),
      queryClient.invalidateQueries({ queryKey: ['project-secrets', variables.projectId] }),
    ]),
  }));
}

export function detachExecutionSecretMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { executionId: string; projectId: string; name: string }) =>
      api.detachExecutionSecret(args.executionId, args.name),
    onSettled: (_data, _err, variables) => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['execution-secrets', variables.executionId] }),
      queryClient.invalidateQueries({ queryKey: ['project-secrets', variables.projectId] }),
    ]),
  }));
}
