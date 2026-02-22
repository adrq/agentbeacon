<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import type { Execution, Agent, Event as BeaconEvent } from '../types';
  import { executionDetailQuery, sessionEventsQuery, cancelExecutionMutation } from '../queries/executions';
  import { agentsQuery } from '../queries/agents';
  import { useQueryClient } from '@tanstack/svelte-query';
  import { connectExecutionSSE } from '../sse';
  import StatusBadge from './StatusBadge.svelte';
  import QuestionBanner from './QuestionBanner.svelte';
  import SessionTree from './SessionTree.svelte';
  import EventsTimeline from './EventsTimeline.svelte';
  import ChatView from './ChatView.svelte';
  import Button from './ui/button.svelte';

  export interface ExecutionPrefill {
    projectId?: string | null;
    agentId?: string;
    prompt?: string;
    title?: string;
  }

  interface Props {
    executionId: string;
    onrerun?: (prefill: ExecutionPrefill) => void;
  }

  let { executionId, onrerun }: Props = $props();

  const terminalStatuses = new Set(['completed', 'failed', 'canceled']);
  const cancellableStatuses = new Set(['working', 'input-required']);

  const queryClient = useQueryClient();
  const agentsQ = agentsQuery();
  let agents = $derived<Agent[]>(agentsQ.data ?? []);

  const detailQuery = executionDetailQuery(() => executionId);
  const cancelMut = cancelExecutionMutation();

  let detail = $derived(detailQuery.data ?? null);
  let loading = $derived(detailQuery.isLoading);
  let error = $derived(detailQuery.error?.message ?? null);

  let selectedSessionId = $state<string | null>(null);

  // Reset selected session when execution changes
  let prevExecId = '';
  $effect.pre(() => {
    if (executionId !== prevExecId) {
      prevExecId = executionId;
      selectedSessionId = null;
    }
  });

  // View toggle: log or chat, persisted to localStorage
  type ViewMode = 'log' | 'chat';
  const storedMode = typeof window !== 'undefined' ? localStorage.getItem('beacon-event-view-mode') : null;
  let viewMode = $state<ViewMode>(storedMode === 'log' || storedMode === 'chat' ? storedMode : 'log');

  $effect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('beacon-event-view-mode', viewMode);
    }
  });

  let masterSession = $derived(detail?.sessions.find(s => !s.parent_session_id) ?? null);
  let displayTitle = $derived(detail?.execution.title ?? executionId.slice(0, 8));
  let isTerminal = $derived(terminalStatuses.has(detail?.execution.status ?? ''));
  let isCancellable = $derived(cancellableStatuses.has(detail?.execution.status ?? ''));

  // SSE connection state
  let sseActive = $state(false);

  // SSE connection lifecycle
  $effect(() => {
    const execId = executionId;
    const terminal = isTerminal;
    const stillLoading = detailQuery.isLoading;
    // Skip SSE for terminal executions and during initial load (avoids brief unnecessary connection)
    if (terminal || stillLoading) {
      sseActive = false;
      return;
    }

    const conn = connectExecutionSSE(
      execId,
      (event: BeaconEvent) => {
        queryClient.setQueryData(
          ['session-events', event.session_id],
          (old: BeaconEvent[] | undefined) => {
            if (!old) return [event];
            if (old.some(e => e.id === event.id)) return old;
            return [...old, event];
          },
        );
        // Status-driving UI (StatusBadge, cancel button, completion summary)
        // reads from the execution detail query. Invalidate it on state changes
        // so those elements update immediately instead of waiting for the 10s poll.
        if (event.event_type === 'state_change') {
          queryClient.invalidateQueries({ queryKey: ['execution', execId] });
          queryClient.invalidateQueries({ queryKey: ['executions'] });
        }
      },
      () => { sseActive = true; },
      () => { sseActive = false; },
    );

    return () => {
      conn.close();
      sseActive = false;
    };
  });

  // Events for the currently viewed session
  let activeSessionId = $derived(selectedSessionId ?? masterSession?.id ?? null);
  const eventsQuery = sessionEventsQuery(
    () => activeSessionId,
    () => isTerminal,
    () => sseActive,
  );
  let events = $derived(eventsQuery.data ?? []);

  // Events for the input-required session (may differ from viewed session)
  let inputSessionId = $derived(
    detail?.sessions.find(s => s.status === 'input-required')?.id ?? activeSessionId
  );
  const inputEventsQuery = sessionEventsQuery(
    () => inputSessionId !== activeSessionId ? inputSessionId : null,
    () => isTerminal,
    () => sseActive,
  );
  // Use input session events if polling separately, otherwise reuse the active session events
  let inputEvents = $derived(
    inputSessionId === activeSessionId ? events : (inputEventsQuery.data ?? [])
  );

  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
  }

  function duration(exec: Execution, endOverride?: string | null): string {
    const start = new Date(exec.created_at).getTime();
    const end = endOverride ? new Date(endOverride).getTime() : (exec.completed_at ? new Date(exec.completed_at).getTime() : Date.now());
    const diff = Math.floor((end - start) / 1000);
    if (diff < 60) return `${diff}s`;
    const m = Math.floor(diff / 60);
    const s = diff % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  function formatDateTime(iso: string): string {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  function handleSessionSelect(sessionId: string | null) {
    selectedSessionId = sessionId;
  }

  // Cancel execution
  let showCancelDialog = $state(false);
  let cancelError: string | null = $state(null);

  async function handleCancel() {
    cancelError = null;
    try {
      await cancelMut.mutateAsync(executionId);
      showCancelDialog = false;
    } catch (e) {
      cancelError = e instanceof Error ? e.message : 'Failed to cancel';
    }
  }

  // Re-run execution
  function handleRerun() {
    if (!detail || !onrerun) return;
    const exec = detail.execution;
    onrerun({
      projectId: exec.project_id,
      agentId: masterSession?.agent_id,
      prompt: exec.input,
      title: exec.title ? `Re-run: ${exec.title}` : undefined,
    });
  }

  // Completion summary helpers
  let terminalLabel = $derived(
    detail?.execution.status === 'completed' ? 'Completed at' :
    detail?.execution.status === 'failed' ? 'Failed at' :
    detail?.execution.status === 'canceled' ? 'Canceled at' : ''
  );
  let completionTime = $derived(detail?.execution.completed_at ?? detail?.execution.updated_at ?? null);
</script>

{#if loading}
  <div class="detail-loading">Loading execution...</div>
{:else if error}
  <div class="detail-error">{error}</div>
{:else if detail}
  <div class="detail-view scroll-thin">
    <div class="detail-header">
      <div class="detail-title-row">
        <h2 class="detail-title">{displayTitle}</h2>
        <StatusBadge status={detail.execution.status} />
        {#if isCancellable}
          <Button variant="destructive" size="sm" disabled={cancelMut.isPending} onclick={() => { cancelError = null; showCancelDialog = true; }}>
            {cancelMut.isPending ? 'Canceling...' : 'Cancel'}
          </Button>
        {/if}
        {#if isTerminal && onrerun}
          <Button variant="secondary" size="sm" onclick={handleRerun}>
            Re-run
          </Button>
        {/if}
      </div>
      <div class="detail-meta">
        {#if masterSession}
          <span>Agent: {agentName(masterSession.agent_id)}</span>
          <span class="meta-sep">&middot;</span>
        {/if}
        <span>{duration(detail.execution)}</span>
      </div>
    </div>

    {#if isTerminal && completionTime}
      <div class="completion-summary">
        <span>{terminalLabel}: {formatDateTime(completionTime)}</span>
        <span class="summary-sep">&middot;</span>
        <span>Elapsed: {duration(detail.execution, completionTime)}</span>
        <span class="summary-sep">&middot;</span>
        <span>{detail.sessions.length} session{detail.sessions.length !== 1 ? 's' : ''}</span>
      </div>
    {/if}

    <QuestionBanner execution={detail.execution} sessions={detail.sessions} events={inputEvents} {agents} />

    {#if detail.sessions.length > 0}
      <SessionTree
        sessions={detail.sessions}
        {agents}
        {selectedSessionId}
        onselectsession={handleSessionSelect}
      />
    {/if}

    <div class="events-header">
      <span class="section-heading">Events</span>
      {#if !isTerminal}
        <span class="sse-indicator" class:connected={sseActive} title={sseActive ? 'Live (SSE)' : 'Polling'}>
          <span class="sse-dot"></span>
          <span class="sse-label">{sseActive ? 'Live' : 'Polling'}</span>
        </span>
      {/if}
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
      <EventsTimeline {events} {agents} sessions={detail.sessions} />
    {:else}
      <ChatView {events} {agents} sessions={detail.sessions} sessionId={activeSessionId} />
    {/if}
  </div>

  <AlertDialog.Root bind:open={showCancelDialog}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay class="modal-overlay" />
      <AlertDialog.Content class="modal-content">
        <AlertDialog.Title class="modal-title">Cancel Execution</AlertDialog.Title>
        <AlertDialog.Description class="modal-description">
          Cancel this execution? The agent will be stopped.
        </AlertDialog.Description>
        {#if cancelError}
          <div class="modal-error">{cancelError}</div>
        {/if}
        <div class="modal-actions">
          <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Keep Running</AlertDialog.Cancel>
          <button class="alert-btn alert-btn-danger" disabled={cancelMut.isPending} onclick={handleCancel}>
            {cancelMut.isPending ? 'Canceling...' : 'Cancel Execution'}
          </button>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  </AlertDialog.Root>
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

  .completion-summary {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 1rem;
    margin: 0 1rem 0.25rem;
    border-radius: 0.375rem;
    background: hsl(var(--muted) / 0.3);
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .summary-sep {
    opacity: 0.4;
  }

  .events-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 1rem 0.375rem;
    flex-shrink: 0;
  }

  .events-header .section-heading {
    margin-right: auto;
  }

  .sse-indicator {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    padding: 0.1875rem 0.5rem 0.1875rem 0.375rem;
    border-radius: 999px;
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.2);
  }

  .sse-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: hsl(var(--muted-foreground) / 0.4);
  }

  .sse-indicator.connected {
    border-color: hsl(var(--status-success) / 0.3);
    background: hsl(var(--status-success) / 0.08);
  }

  .sse-indicator.connected .sse-dot {
    background: hsl(var(--status-success));
    box-shadow: 0 0 6px hsl(var(--status-success) / 0.5);
  }

  .sse-indicator.connected .sse-label {
    color: hsl(var(--status-success));
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

  .modal-error {
    padding: 0.375rem 0.625rem;
    border-radius: 0.25rem;
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
    margin-bottom: 1rem;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
