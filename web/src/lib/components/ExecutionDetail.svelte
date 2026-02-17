<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { ExecutionDetail as ExecDetail, Event } from '../types';
  import { api } from '../api';
  import { agents as agentsStore } from '../stores/executions';
  import StatusBadge from './StatusBadge.svelte';
  import QuestionBanner from './QuestionBanner.svelte';
  import SessionTree from './SessionTree.svelte';
  import EventsTimeline from './EventsTimeline.svelte';
  import ChatView from './ChatView.svelte';

  interface Props {
    executionId: string;
  }

  let { executionId }: Props = $props();

  let execution = $state<ExecDetail | null>(null);
  let events = $state<Event[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let selectedSessionId = $state<string | null>(null);

  // View toggle: log or chat, persisted to localStorage
  type ViewMode = 'log' | 'chat';
  const storedMode = typeof window !== 'undefined' ? localStorage.getItem('beacon-event-view-mode') : null;
  let viewMode = $state<ViewMode>(storedMode === 'log' || storedMode === 'chat' ? storedMode : 'log');

  $effect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('beacon-event-view-mode', viewMode);
    }
  });

  let detailTimer: ReturnType<typeof setInterval> | null = null;
  let prevExecId = '';

  const terminalStatuses = new Set(['completed', 'failed', 'canceled']);

  let agents = $derived($agentsStore);
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

    <div class="events-header">
      <span class="section-heading">Events</span>
      <div class="view-toggle" role="tablist" aria-label="Event view mode">
        <button
          class="toggle-btn"
          class:active={viewMode === 'log'}
          role="tab"
          aria-selected={viewMode === 'log'}
          onclick={() => viewMode = 'log'}
        >Log</button>
        <button
          class="toggle-btn"
          class:active={viewMode === 'chat'}
          role="tab"
          aria-selected={viewMode === 'chat'}
          onclick={() => viewMode = 'chat'}
        >Chat</button>
      </div>
    </div>

    {#if viewMode === 'log'}
      <EventsTimeline {events} />
    {:else}
      <ChatView {events} {agents} sessions={execution.sessions} sessionId={selectedSessionId ?? masterSession?.id ?? null} />
    {/if}
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
    font-size: 1.25rem;
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

  .events-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 1rem 0.375rem;
    flex-shrink: 0;
  }

  .view-toggle {
    display: flex;
    border: 1px solid hsl(var(--border));
    border-radius: 0.375rem;
    overflow: hidden;
  }

  .toggle-btn {
    padding: 0.1875rem 0.625rem;
    font-size: 0.6875rem;
    font-weight: 500;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: background 0.1s, color 0.1s;
  }

  .toggle-btn:not(:last-child) {
    border-right: 1px solid hsl(var(--border));
  }

  .toggle-btn.active {
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
    font-weight: 600;
  }

  .toggle-btn:hover:not(.active) {
    background: hsl(var(--muted) / 0.5);
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
