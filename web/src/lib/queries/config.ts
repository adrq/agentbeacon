import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api } from '../api';

export function configQuery() {
  return createQuery(() => ({
    queryKey: ['config'],
    queryFn: () => api.getConfig(),
  }));
}

export function updateConfigMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { name: string; value: string }) =>
      api.updateConfig(args.name, args.value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] });
    },
  }));
}
