import { createQuery, createMutation, useQueryClient } from '@tanstack/svelte-query';
import { api } from '../api';
import type { ConfigEntry } from '../types';

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
    onSuccess: (updated) => {
      // Optimistically update the cached list so the textarea doesn't revert
      // while the refetch is in flight.
      queryClient.setQueryData<ConfigEntry[]>(['config'], (prev) =>
        prev ? prev.map((e) => (e.name === updated.name ? updated : e)) : [updated],
      );
      queryClient.invalidateQueries({ queryKey: ['config'] });
    },
  }));
}
