// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

// Re-reading a cached query after the session it belongs to is released.
import type { QueryClient } from '@tanstack/svelte-query';

/**
 * Refetch `key` and resolve once a read started by this call has landed.
 *
 * Cancels any read in flight first, then requires the entry to hold status
 * `success`, an idle fetch status, and `dataUpdatedAt` no earlier than the
 * call; throws otherwise.
 *
 * Resolves true when the entry was read. Resolves false, having removed the
 * entry, when it holds neither data nor an error and has no observers.
 * `now` is injectable for tests.
 */
export async function readAfterRelease(
  queryClient: QueryClient,
  key: readonly unknown[],
  now: () => number = Date.now,
): Promise<boolean> {
  const startedAt = now();
  // Rejects with the cancelled read's error, which is not this call's.
  await queryClient.cancelQueries({ queryKey: key, exact: true }).catch(() => {});

  const cancelled = queryClient.getQueryState(key);
  if (cancelled && cancelled.dataUpdatedAt === 0 && cancelled.errorUpdatedAt === 0) {
    const watchers =
      queryClient.getQueryCache().find({ queryKey: key, exact: true })?.getObserversCount() ?? 0;
    if (watchers === 0) {
      queryClient.removeQueries({ queryKey: key, exact: true });
      return false;
    }
  }

  await queryClient.refetchQueries({ queryKey: key, exact: true }, { throwOnError: true });

  const state = queryClient.getQueryState(key);
  if (!state || state.status !== 'success' || state.fetchStatus !== 'idle') {
    throw new Error('history read did not land');
  }
  if (state.dataUpdatedAt < startedAt) throw new Error('history read predates release');
  return true;
}
