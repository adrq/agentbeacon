<script lang="ts">
  import { useQueryClient } from '@tanstack/svelte-query';
  import type { Event, Execution, Agent } from '../types';
  import { api } from '../api';
  import { inputRequiredSessionsQuery } from '../queries/executions';
  import { extractQuestions } from '../questions';
  import {
    decisionItems, suppressedSessions, submittedBatches, notifyNewDecision,
    noQuestionExecutions,
    type DecisionItem,
  } from '../stores/questionState';

  const queryClient = useQueryClient();
  const sessionsQuery = inputRequiredSessionsQuery();

  let effectVersion = 0;
  let prevItemIds = new Set<string>();
  // Last-known-good cache: retain previous DecisionItem for sessions whose fetch fails
  let itemCache = new Map<string, DecisionItem>();

  $effect(() => {
    const sessions = sessionsQuery.data;
    if (!sessions) return;

    const version = ++effectVersion;

    // Clean up suppressions for sessions no longer in the response
    const activeSessionIds = new Set(sessions.map(s => s.id));
    suppressedSessions.update(suppressed => {
      const next: Record<string, string> = {};
      let changed = false;
      for (const sid in suppressed) {
        if (activeSessionIds.has(sid)) {
          next[sid] = suppressed[sid];
        } else {
          changed = true;
        }
      }
      return changed ? next : suppressed;
    });
    submittedBatches.update(submitted => {
      const next: Record<string, string> = {};
      let changed = false;
      for (const sid in submitted) {
        if (activeSessionIds.has(sid)) {
          next[sid] = submitted[sid];
        } else {
          changed = true;
        }
      }
      return changed ? next : submitted;
    });

    // Prune cache for sessions no longer active
    for (const sid of itemCache.keys()) {
      if (!activeSessionIds.has(sid)) itemCache.delete(sid);
    }

    // Build execution/agent maps from TanStack Query caches
    const allExecEntries = queryClient.getQueriesData<Execution[]>({ queryKey: ['executions'] });
    const execMap = new Map<string, Execution>();
    for (const [, data] of allExecEntries) {
      if (data) for (const e of data) execMap.set(e.id, e);
    }
    const ags: Agent[] = queryClient.getQueryData(['agents']) ?? [];

    // Fetch events for ALL sessions (suppression is content-based, handled at display level)
    Promise.allSettled(
      sessions.map(async (session) => {
        const events: Event[] = await queryClient.fetchQuery({
          queryKey: ['session-events', session.id],
          queryFn: () => api.getSessionEvents(session.id),
          staleTime: 2000,
        });
        return { session, events };
      })
    ).then(results => {
      if (version !== effectVersion) return;

      const fetchedSessionIds = new Set<string>();

      for (const result of results) {
        if (result.status !== 'fulfilled') continue;
        const { session, events } = result.value;
        fetchedSessionIds.add(session.id);
        const { batchId, questions } = extractQuestions(events);
        if (questions.length === 0) {
          itemCache.delete(session.id);
          continue;
        }

        const exec = execMap.get(session.execution_id);
        const agent = ags.find(a => a.id === session.agent_id);

        itemCache.set(session.id, {
          sessionId: session.id,
          executionId: session.execution_id,
          executionTitle: exec?.title ?? null,
          agentName: agent?.name ?? session.agent_id.slice(0, 8),
          batchId,
          questions,
          createdAt: session.created_at,
        });
      }
      // Failed fetches retain their previous cache entry (no update, no delete)

      // Track executions that were fetched successfully with no questions
      const noQExecs = new Set<string>();
      for (const result of results) {
        if (result.status !== 'fulfilled') continue;
        const { session, events: evts } = result.value;
        const { questions: qs } = extractQuestions(evts);
        if (qs.length === 0) noQExecs.add(session.execution_id);
      }
      // Remove any that DO have questions (multi-session: one session has, one doesn't)
      for (const item of itemCache.values()) noQExecs.delete(item.executionId);
      noQuestionExecutions.set(noQExecs);

      const newItems = [...itemCache.values()]
        .sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());

      // Notify about newly appeared items (keyed on session+batch for multi-turn)
      const newItemIds = new Set(newItems.map(i => `${i.sessionId}:${i.batchId}`));
      for (const item of newItems) {
        if (!prevItemIds.has(`${item.sessionId}:${item.batchId}`)) {
          notifyNewDecision(item);
        }
      }
      prevItemIds = newItemIds;

      decisionItems.set(newItems);
    });
  });
</script>
