<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { ExecutionDetail as ExecDetail, Event, Agent } from '../types';
  import { api } from '../api';
  import StatusBadge from './StatusBadge.svelte';
  import QuestionBanner from './QuestionBanner.svelte';
  import SessionTree from './SessionTree.svelte';
  import EventsTimeline from './EventsTimeline.svelte';

  interface Props {
    executionId: string;
  }

  let { executionId }: Props = $props();

  let execution = $state<ExecDetail | null>(null);
  let events = $state<Event[]>([]);
  let agents = $state<Agent[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let selectedSessionId = $state<string | null>(null);

  let detailTimer: ReturnType<typeof setInterval> | null = null;
  let prevExecId = '';

  const terminalStatuses = new Set(['completed', 'failed', 'canceled']);

  let masterSession = $derived(execution?.sessions.find(s => !s.parent_session_id) ?? null);
  let displayTitle = $derived(execution?.title ?? executionId.slice(0, 8));

  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
  }

  function duration(exec: ExecDetail): string {
    const start = new Date(exec.created_at).getTime();
    const end = exec.completed_at ? new Date(exec.completed_at).getTime() : Date.now();
    const diff = Math.floor((end - start) / 1000);
    if (diff < 60) return `${diff}s`;
    const m = Math.floor(diff / 60);
    const s = diff % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  async function poll() {
    const id = prevExecId;
    try {
      const result = await api.getExecution(id);
      if (prevExecId !== id) return;
      execution = result;
      error = null;

      if (terminalStatuses.has(result.status)) {
        stopPolling();
      }
    } catch (e) {
      if (prevExecId !== id) return;
      execution = null;
      error = e instanceof Error ? e.message : 'Failed to load';
    } finally {
      loading = false;
    }

    const session = execution?.sessions.find(s => !s.parent_session_id);
    if (!session) return;
    const sid = selectedSessionId ?? session.id;
    try {
      const evs = await api.getSessionEvents(sid);
      if (prevExecId !== id) return;
      events = evs;
    } catch {
      // retry next poll
    }
  }

  async function fetchAgents() {
    try {
      agents = await api.getAgents();
    } catch {
      // Non-critical
    }
  }

  function stopPolling() {
    if (detailTimer) { clearInterval(detailTimer); detailTimer = null; }
  }

  function startForId(id: string) {
    stopPolling();
    prevExecId = id;
    loading = true;
    execution = null;
    events = [];
    selectedSessionId = null;
    fetchAgents();
    poll();
    detailTimer = setInterval(poll, 3000);
  }

  $effect.pre(() => {
    if (executionId !== prevExecId) {
      startForId(executionId);
    }
  });

  onDestroy(stopPolling);

  function handleSessionSelect(sessionId: string | null) {
    selectedSessionId = sessionId;
    const session = execution?.sessions.find(s => !s.parent_session_id);
    if (!session) return;
    const sid = sessionId ?? session.id;
    const capturedExecId = executionId;
    const capturedSid = sid;
    api.getSessionEvents(sid).then(evs => {
      if (prevExecId === capturedExecId && (selectedSessionId ?? session.id) === capturedSid) {
        events = evs;
      }
    }).catch(() => {});
  }
</script>

{#if loading}
  <div class="detail-loading">Loading execution...</div>
{:else if error}
  <div class="detail-error">{error}</div>
{:else if execution}
  <div class="detail-view scroll-thin">
    <div class="detail-header">
      <div class="detail-title-row">
        <h2 class="detail-title">{displayTitle}</h2>
        <StatusBadge status={execution.status} />
      </div>
      <div class="detail-meta">
        {#if masterSession}
          <span>Agent: {agentName(masterSession.agent_id)}</span>
          <span class="meta-sep">&middot;</span>
        {/if}
        <span>{duration(execution)}</span>
      </div>
    </div>

    {#if execution.status === 'input-required'}
      <QuestionBanner {execution} {agents} />
    {/if}

    {#if execution.sessions.length > 0}
      <SessionTree
        sessions={execution.sessions}
        {agents}
        {selectedSessionId}
        onselectsession={handleSessionSelect}
      />
    {/if}

    <EventsTimeline events={events} />
  </div>
{/if}

<style>
  .detail-view {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .detail-header {
    padding: 1rem 1rem 0.5rem;
    flex-shrink: 0;
  }

  .detail-title-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  .detail-title {
    font-size: 1.125rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .detail-meta {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    margin-top: 0.25rem;
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .meta-sep {
    opacity: 0.5;
  }

  .detail-loading, .detail-error {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
  }

  .detail-error {
    color: hsl(var(--status-danger));
  }
</style>
