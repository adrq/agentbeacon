<script lang="ts">
  import { useQueryClient } from '@tanstack/svelte-query';
  import type { Event, Execution, Agent } from '../types';
  import { api } from '../api';
  import { executionsQuery } from '../queries/executions';
  import { agentsQuery } from '../queries/agents';
  import { inputRequiredSessionsQuery } from '../queries/executions';
  import { extractQuestions } from '../questions';
  import type { QuestionState } from '../questions';
  import DecisionCard from './DecisionCard.svelte';

  interface QueueItem {
    sessionId: string;
    executionId: string;
    executionTitle: string | null;
    agentName: string;
    questions: QuestionState[];
    createdAt: string;
  }

  const queryClient = useQueryClient();
  const execsQuery = executionsQuery();
  const agsQuery = agentsQuery();
  const sessionsQuery = inputRequiredSessionsQuery();

  let items: QueueItem[] = $state([]);
  let loading = $derived(sessionsQuery.isLoading);
  let error: string | null = $state(null);

  // Sessions we've answered — suppress until next poll confirms they're gone
  let suppressed: Record<string, true> = $state({});

  let visibleItems = $derived(items.filter(i => !suppressed[i.sessionId]));

  // Fetch events for input-required sessions and build queue items
  let effectVersion = 0;

  $effect(() => {
    const sessions = sessionsQuery.data;
    if (!sessions) return;

    const version = ++effectVersion;

    // Clean up suppressions for sessions no longer in the response
    const activeSessionIds = new Set(sessions.map(s => s.id));
    const next: Record<string, true> = {};
    let changed = false;
    for (const sid in suppressed) {
      if (activeSessionIds.has(sid)) {
        next[sid] = true;
      } else {
        changed = true;
      }
    }
    if (changed) suppressed = next;

    // Search all execution query caches (any project filter) and deduplicate
    const allExecEntries = queryClient.getQueriesData<Execution[]>({ queryKey: ['executions'] });
    const execMap = new Map<string, Execution>();
    for (const [, data] of allExecEntries) {
      if (data) for (const e of data) execMap.set(e.id, e);
    }
    const execs = Array.from(execMap.values());
    const ags: Agent[] = queryClient.getQueryData(['agents']) ?? [];

    const unsuppressed = sessions.filter(s => !suppressed[s.id]);

    // Fan out event fetches for each input-required session
    Promise.allSettled(
      unsuppressed.map(async (session) => {
        const events: Event[] = await queryClient.fetchQuery({
          queryKey: ['session-events', session.id],
          queryFn: () => api.getSessionEvents(session.id),
          staleTime: 2000,
        });
        return { session, events };
      })
    ).then(results => {
      if (version !== effectVersion) return;

      const newItems: QueueItem[] = [];

      for (const result of results) {
        if (result.status !== 'fulfilled') continue;
        const { session, events } = result.value;
        const questions = extractQuestions(events);
        if (questions.length === 0) continue;

        const exec = execs.find(e => e.id === session.execution_id);
        const agent = ags.find(a => a.id === session.agent_id);

        newItems.push({
          sessionId: session.id,
          executionId: session.execution_id,
          executionTitle: exec?.title ?? null,
          agentName: agent?.name ?? session.agent_id.slice(0, 8),
          questions,
          createdAt: session.created_at,
        });
      }

      newItems.sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
      items = newItems;
      error = null;
    }).catch(e => {
      if (version !== effectVersion) return;
      error = e instanceof Error ? e.message : 'Failed to load questions';
    });
  });

  function handleSubmitted(sessionId: string) {
    suppressed = { ...suppressed, [sessionId]: true };
  }
</script>

<div class="decision-queue">
  {#if loading}
    <div class="queue-loading">Loading questions...</div>
  {:else if error}
    <div class="queue-error">{error}</div>
  {:else if visibleItems.length === 0}
    <div class="queue-empty">
      <span class="queue-empty-icon">&#x2713;</span>
      <span>No questions pending</span>
    </div>
  {:else}
    <div class="queue-header">
      <span class="queue-count">{visibleItems.length} question{visibleItems.length > 1 ? 's' : ''} pending</span>
    </div>
    <div class="queue-list">
      {#each visibleItems as item (item.sessionId)}
        <DecisionCard
          sessionId={item.sessionId}
          executionId={item.executionId}
          executionTitle={item.executionTitle}
          agentName={item.agentName}
          questions={item.questions}
          onsubmitted={handleSubmitted}
        />
      {/each}
    </div>
  {/if}
</div>

<style>
  .decision-queue {
    padding: 1rem;
  }

  .queue-loading {
    text-align: center;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    padding: 2rem 0;
  }

  .queue-error {
    text-align: center;
    font-size: 0.8125rem;
    color: hsl(var(--status-danger));
    padding: 1.5rem;
    border: 1px dashed hsl(var(--status-danger) / 0.3);
    border-radius: 0.5rem;
  }

  .queue-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    padding: 1.5rem;
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
    border: 1px dashed hsl(var(--border));
    border-radius: 0.5rem;
  }

  .queue-empty-icon {
    color: hsl(var(--status-success));
    font-weight: 700;
  }

  .queue-header {
    margin-bottom: 0.75rem;
  }

  .queue-count {
    font-size: 0.8125rem;
    font-weight: 600;
    color: hsl(var(--status-attention));
  }

  .queue-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
</style>
