import { createQuery, createMutation, useQueryClient, type QueryClient } from '@tanstack/svelte-query';
import type { AgentPoolEntry, CreateExecutionResponse, ExecutionDetail, Event, Page } from '../types';
import { api } from '../api';
import { heldLiveEvents, releaseCount } from '../liveEvents';
import { deferTail, tailHeld } from '../tailHold';
import { appendHeld, generation, mergeAnchored, windowKey } from '../historyWindow';
import {
  HISTORY_PAGE_LIMIT,
  WINDOW_PAGE_LIMIT,
  cachedIds,
  commitNewestWindow,
  fetchNewestWindow,
  fetchOlderPage,
  fetchSessionHistory,
  spliceSessionFullHistory,
  spliceSessionHistory,
} from '../historyReads';

export {
  WINDOW_PAGE_LIMIT,
  fetchNewestWindow,
  fetchOlderPage,
  fetchSessionHistory,
  spliceSessionFullHistory,
  spliceSessionHistory,
};

/**
 * Merge a stored read against the cache at the moment it is stored.
 *
 * While `sessionId`'s list is holding its head, the cache keeps what it has and
 * the merge is queued for the release instead.
 */
function reconcileHistory(
  queryClient: QueryClient,
  key: readonly unknown[],
  sessionId: string | null | undefined,
  oldData: unknown,
  newData: unknown,
): unknown {
  const cached = oldData as Event[] | undefined;
  if (sessionId && cached && tailHeld(sessionId)) {
    const rows = newData as Event[];
    deferTail(sessionId, () => {
      // A removed entry is not written back.
      if (!queryClient.getQueryState(key)) return;
      queryClient.setQueryData<Event[]>(key, current => mergeAnchored(current, rows));
    });
    return cached;
  }
  return mergeAnchored(cached, newData as Event[]);
}


export function executionsQuery(projectId?: () => string | null | undefined) {
  return createQuery(() => ({
    queryKey: ['executions', { projectId: projectId?.() || undefined }],
    queryFn: () => api.getExecutions({
      project_id: projectId?.() || undefined,
    }),
    // A caller that asked to filter waits until it has something to filter by.
    // Callers that pass no getter are asking for everything and run.
    enabled: projectId === undefined || !!projectId(),
    refetchInterval: 5000,
    gcTime: 10_000,
  }));
}

export function executionDetailQuery(id: () => string | null) {
  return createQuery(() => ({
    queryKey: ['execution', id()],
    queryFn: ({ queryKey }) => api.getExecution(queryKey[1] as string),
    enabled: !!id(),
    refetchInterval: (query) => {
      const data = query.state.data as ExecutionDetail | undefined;
      if (!data) return 10_000;
      // A session is released when its outcome is set and no worker or command
      // remains. Polling continues until every session is released, which is
      // later than the outcomes appearing.
      const allReleased = data.execution.outcome != null
        && data.sessions.every(
          s => s.outcome != null && s.worker_id == null && s.command_type == null,
        );
      if (allReleased) return false;
      return 10_000;
    },
  }));
}

export function sessionEventsQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
  sseActive?: () => boolean,
) {
  const queryClient = useQueryClient();
  return createQuery(() => {
  // Read once per options instance, not at commit time.
  const id = sessionId();
  return ({
    queryKey: ['session-events', id],
    // Reads back to the first row the cache already covers, then merges there.
    // The id comes from the key being read, not the current selection.
    queryFn: async ({ signal, queryKey }) => {
      const id = queryKey[1] as string;
      const key = ['session-events', id];
      // Re-reads while new arrivals overflowed the hold; the cached ids are
      // re-read each cycle. With nothing cached, one page; otherwise newest
      // pages until one overlaps the cache.
      // A cold read returns its page so the edge can be recorded from the one
      // that is kept; re-reads discard the pages they replace.
      const captured = generation(windowKey(id));
      // Held rows to treat as accounted for if none is visible in the read.
      const heldBefore = new Set(heldLiveEvents(id).map(e => e.id));
      const read = async (): Promise<{ rows: Event[]; page: Page<Event> | null }> => {
        if (queryClient.getQueryData<Event[]>(key)?.length) {
          return {
            rows: await fetchSessionHistory(id, {
              signal,
              stopAt: cachedIds(queryClient, key),
              limit: WINDOW_PAGE_LIMIT,
            }),
            page: null,
          };
        }
        const page = await fetchNewestWindow(id, { signal });
        return { rows: page.items, page };
      };

      let releases = releaseCount(id);
      let fetched = await read();
      while (releaseCount(id) !== releases) {
        releases = releaseCount(id);
        fetched = await read();
      }
      // A cold read decides it is cold before it starts. If rows landed while it
      // was out, its page may not join them — nothing shared means nothing to
      // merge at, and the two would be concatenated in whichever order they
      // finished. Walk to an overlap instead.
      if (fetched.page && queryClient.getQueryData<Event[]>(key)?.length) {
        fetched = {
          rows: await fetchSessionHistory(id, {
            signal,
            stopAt: cachedIds(queryClient, key),
            limit: WINDOW_PAGE_LIMIT,
          }),
          page: null,
        };
      }
      if (fetched.page) commitNewestWindow(id, fetched.page, { signal, captured });
      return appendHeld(fetched.rows, id, {
        incorporated: cachedIds(queryClient, key),
        heldBefore,
      });
    },
    enabled: !!sessionId(),
    // Settled sessions are immutable: never refetch on remount. Live sessions
    // rely on SSE + the poll below, so a longer staleTime only suppresses
    // redundant refetch-on-remount during rapid navigation.
    staleTime: () => (isTerminal?.() ? Infinity : 30_000),
    // Release large parsed histories sooner than the 5-min default to bound
    // retained peak memory.
    gcTime: 120_000,
    // Merges against the cache as it stands when the read is stored. Replaces
    // the default deep comparison; rows are compared by id only.
    structuralSharing: (o: unknown, n: unknown) =>
      reconcileHistory(queryClient, ['session-events', id], id, o, n),
    refetchInterval: () => {
      if (isTerminal?.()) return false;
      if (sseActive?.()) return false;
      return 3000;
    },
  });
  });
}

/** A session's whole history, on its own key. */
export function sessionEventsFullQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
  sseActive?: () => boolean,
) {
  const queryClient = useQueryClient();
  return createQuery(() => {
  const id = sessionId();
  return ({
    queryKey: ['session-events-full', id],
    queryFn: async ({ signal, queryKey }) => {
      const id = queryKey[1] as string;
      const key = ['session-events-full', id];
      // Held rows to treat as accounted for if none is visible in the read.
      const heldBefore = new Set(heldLiveEvents(id).map(e => e.id));
      let releases = releaseCount(id);
      let fetched = await fetchSessionHistory(id, { signal, stopAt: cachedIds(queryClient, key) });
      while (releaseCount(id) !== releases) {
        releases = releaseCount(id);
        fetched = await fetchSessionHistory(id, { signal, stopAt: cachedIds(queryClient, key) });
      }
      return appendHeld(fetched, id, {
        incorporated: cachedIds(queryClient, key),
        heldBefore,
      });
    },
    enabled: !!sessionId(),
    staleTime: () => (isTerminal?.() ? Infinity : 30_000),
    gcTime: 120_000,
    structuralSharing: (o: unknown, n: unknown) =>
      reconcileHistory(queryClient, ['session-events-full', id], id, o, n),
    refetchInterval: () => {
      if (isTerminal?.()) return false;
      if (sseActive?.()) return false;
      return 3000;
    },
  });
  });
}

export function sessionBranchesQuery(
  sessionId: () => string | null | undefined,
  isTerminal?: () => boolean,
) {
  return createQuery(() => ({
    queryKey: ['session-branches', sessionId()],
    queryFn: ({ queryKey }) => api.getSessionBranches(queryKey[1] as string),
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
    queryFn: ({ queryKey }) => api.getSessionDiff(queryKey[1] as string, { base: queryKey[2] as string | undefined }),
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
    queryFn: ({ queryKey }) => api.getExecutionAgents(queryKey[1] as string),
    enabled: !!executionId(),
  }));
}

export function addExecutionAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { executionId: string; agentId: string; addToProject: boolean; projectId: string | null }) =>
      api.addExecutionAgent(args.executionId, { agent_id: args.agentId, add_to_project: args.addToProject }),
    // Returned so mutateAsync resolves only after the pool has refetched.
    onSettled: (_data, _err, variables) => Promise.all([
      queryClient.invalidateQueries({ queryKey: ['execution-agents', variables.executionId] }),
      variables.addToProject && variables.projectId
        ? queryClient.invalidateQueries({ queryKey: ['project-agents', variables.projectId] })
        : undefined,
    ]),
  }));
}

export function removeExecutionAgentMutation() {
  const queryClient = useQueryClient();
  return createMutation(() => ({
    mutationFn: (args: { executionId: string; agentId: string }) =>
      api.removeExecutionAgent(args.executionId, args.agentId),
    // Drop the row immediately so a failed follow-up refetch cannot leave a
    // stale member that invites a repeat DELETE.
    onSuccess: (_data, variables) => {
      queryClient.setQueryData<AgentPoolEntry[]>(['execution-agents', variables.executionId],
        old => old?.filter(a => a.agent_id !== variables.agentId));
    },
    // Returned so the row stays pending until the refetch settles. A failed
    // refetch resolves (invalidateQueries does not throw), so it never turns
    // a successful removal into a reported failure.
    onSettled: (_data, _err, variables) =>
      queryClient.invalidateQueries({ queryKey: ['execution-agents', variables.executionId] }),
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
      sandbox_policy?: { fs_level: string };
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
    queryFn: ({ queryKey }) => api.getExecutionSessions(queryKey[1] as string),
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
