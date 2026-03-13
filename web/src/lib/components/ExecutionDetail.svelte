<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import type { Execution, Agent, Event as BeaconEvent, EphemeralEvent, MessagePayload } from '../types';
  import { executionDetailQuery, sessionEventsQuery, cancelExecutionMutation, completeExecutionMutation, executionAgentsQuery, recoverSessionMutation } from '../queries/executions';
  import { agentsQuery } from '../queries/agents';
  import { useQueryClient } from '@tanstack/svelte-query';
  import { connectExecutionSSE, type SSEConnection } from '../sse';
  import StatusBadge from './StatusBadge.svelte';
  import QuestionBanner from './QuestionBanner.svelte';
  import SessionTree from './SessionTree.svelte';
  import EventsTimeline from './EventsTimeline.svelte';
  import ChatView from './ChatView.svelte';
  import DiffPanel from './DiffPanel.svelte';
  import ElapsedTime from './ElapsedTime.svelte';
  import { executionsWithQuestions, noQuestionExecutions } from '../stores/questionState';
  import Button from './ui/button.svelte';
  import { openSearchTab } from '../stores/wikiState.svelte';
  import { router } from '../router';
  import { executionPrefill } from '../stores/appState';
  import type { EventFilter } from '../eventFilterGroups';

  interface Props {
    executionId: string;
  }

  let { executionId }: Props = $props();

  const terminalStatuses = new Set(['completed', 'failed', 'canceled']);
  const cancellableStatuses = new Set(['working', 'input-required']);

  const queryClient = useQueryClient();
  const agentsQ = agentsQuery();
  let agents = $derived<Agent[]>(agentsQ.data ?? []);

  const detailQuery = executionDetailQuery(() => executionId);
  const poolQuery = executionAgentsQuery(() => executionId);
  const cancelMut = cancelExecutionMutation();
  const completeMut = completeExecutionMutation();
  const recoverMut = recoverSessionMutation();

  let detail = $derived(detailQuery.data ?? null);
  let loading = $derived(detailQuery.isLoading);
  let error = $derived(detailQuery.error?.message ?? null);

  let selectedSessionId = $state<string | null>(null);

  // Ephemeral streaming state (not in TanStack cache — transient)
  let ephemeralBuffers = $state<Map<string, { text: string; lastSeq: number }>>(new Map());
  let ephemeralThinkingBuffers = $state<Map<string, { text: string; lastSeq: number; startedAt: string }>>(new Map());
  let settledThinkingDurations = $state<Map<string, { durationMs: number; startedAt: string }>>(new Map());
  let lastPersistedSeq = new Map<string, number>();

  // Event filter state (shared between Chat and Log views, resets on exec change)
  let eventFilter = $state<EventFilter>('all');

  // SSE connection state (declared before $effect.pre that references them)
  let sseActive = $state(false);
  let sseReconnecting = $state(false);
  let sseConnection = $state<SSEConnection | null>(null);

  // Reset selected session and ephemeral state when execution changes
  let prevExecId = '';
  $effect.pre(() => {
    if (executionId !== prevExecId) {
      prevExecId = executionId;
      selectedSessionId = null;
      eventFilter = 'all';
      const hashView = getHashViewParam();
      if (hashView) viewMode = hashView;
      lastPersistedSeq.clear();
      ephemeralBuffers = new Map();
      ephemeralThinkingBuffers = new Map();
      sseReconnecting = false;
      sseConnection = null;
    }
  });

  // View toggle: log, chat, or diff — persisted to both URL hash param and localStorage
  type ViewMode = 'log' | 'chat' | 'diff';
  const validModes = new Set<string>(['log', 'chat', 'diff']);

  function getHashViewParam(): ViewMode | null {
    try {
      const hash = window.location.hash;
      const qIdx = hash.indexOf('?');
      if (qIdx === -1) return null;
      const params = new URLSearchParams(hash.slice(qIdx + 1));
      const v = params.get('view');
      return v && validModes.has(v) ? v as ViewMode : null;
    } catch { return null; }
  }

  function setHashViewParam(mode: ViewMode) {
    try {
      const hash = window.location.hash;
      const qIdx = hash.indexOf('?');
      const basePath = qIdx === -1 ? hash : hash.slice(0, qIdx);
      const params = qIdx === -1 ? new URLSearchParams() : new URLSearchParams(hash.slice(qIdx + 1));
      if (mode === 'log') {
        params.delete('view');
      } else {
        params.set('view', mode);
      }
      const qs = params.toString();
      const newHash = qs ? `${basePath}?${qs}` : basePath;
      if (window.location.hash !== newHash) {
        history.replaceState(null, '', newHash);
      }
    } catch { /* navigation unavailable */ }
  }

  // Initialize: URL hash param takes priority, then localStorage, then default 'log'
  let hashMode = typeof window !== 'undefined' ? getHashViewParam() : null;
  let storedMode: string | null = null;
  try { storedMode = typeof window !== 'undefined' ? localStorage.getItem('agentbeacon-event-view-mode') : null; } catch { /* localStorage unavailable */ }
  let viewMode = $state<ViewMode>(
    hashMode ?? (storedMode && validModes.has(storedMode) ? storedMode as ViewMode : 'log')
  );

  $effect(() => {
    try { if (typeof window !== 'undefined') localStorage.setItem('agentbeacon-event-view-mode', viewMode); } catch { /* localStorage unavailable */ }
    setHashViewParam(viewMode);
  });

  let leadSession = $derived(detail?.sessions.find(s => !s.parent_session_id) ?? null);
  let displayTitle = $derived(detail?.execution.title ?? executionId.slice(0, 8));
  let isTerminal = $derived(terminalStatuses.has(detail?.execution.status ?? ''));
  let isCancellable = $derived(cancellableStatuses.has(detail?.execution.status ?? ''));
  let isCompletable = $derived(
    detail?.execution.status === 'working' || detail?.execution.status === 'input-required'
  );
  let isRecoverable = $derived(
    detail?.execution.status === 'failed' && leadSession?.status === 'failed' && leadSession?.agent_session_id != null
  );

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

        // Update stale-delta tracking for all persisted messages
        if (event.event_type === 'message' && event.session_id) {
          lastPersistedSeq.set(event.session_id, Math.max(
            lastPersistedSeq.get(event.session_id) ?? 0,
            event.msg_seq ?? 0,
          ));
          // Clear ephemeral buffer only on persisted text (non-text mid-turn would cause flickering)
          const payload = event.payload as MessagePayload;
          const hasText = payload.parts?.some((p: { kind: string }) => p.kind === 'text');
          if (hasText) {
            const buf = ephemeralBuffers.get(event.session_id);
            if (buf && (event.msg_seq ?? 0) >= buf.lastSeq) {
              ephemeralBuffers.delete(event.session_id);
              ephemeralBuffers = new Map(ephemeralBuffers);
            }
          }
          // Clear ephemeral thinking buffer only when the persisted message
          // contains a complete thinking block. Clearing on any message would
          // drop in-flight reasoning if text/tool output is persisted first.
          const thinkBuf = ephemeralThinkingBuffers.get(event.session_id);
          if (thinkBuf && (event.msg_seq ?? 0) >= thinkBuf.lastSeq) {
            const hasPersistedThinking = payload.parts?.some(
              (p: { kind: string; data?: unknown }) =>
                p.kind === 'data' &&
                (p.data as Record<string, unknown>)?.type === 'thinking'
            );
            if (hasPersistedThinking) {
              settledThinkingDurations.set(event.session_id, {
                durationMs: Date.now() - new Date(thinkBuf.startedAt).getTime(),
                startedAt: thinkBuf.startedAt,
              });
              settledThinkingDurations = new Map(settledThinkingDurations);
              ephemeralThinkingBuffers.delete(event.session_id);
              ephemeralThinkingBuffers = new Map(ephemeralThinkingBuffers);
            }
          }
        }

        // Status-driving UI (StatusBadge, cancel button, completion summary)
        // reads from the execution detail query. Invalidate it on state changes
        // so those elements update immediately instead of waiting for the 10s poll.
        if (event.event_type === 'state_change') {
          queryClient.invalidateQueries({ queryKey: ['execution', execId] });
          queryClient.invalidateQueries({ queryKey: ['executions'] });
          queryClient.invalidateQueries({ queryKey: ['session-diff'] });
          if (event.session_id) {
            const p = event.payload as { to?: string };
            if (p.to === 'working') {
              // New turn starting — reset stale-delta guard, stale duration, and stale thinking buffer
              lastPersistedSeq.delete(event.session_id);
              settledThinkingDurations.delete(event.session_id);
              settledThinkingDurations = new Map(settledThinkingDurations);
              ephemeralThinkingBuffers.delete(event.session_id);
              ephemeralThinkingBuffers = new Map(ephemeralThinkingBuffers);
            } else {
              // Turn ended — clear buffer and poison guard against late retried POSTs
              if (ephemeralBuffers.has(event.session_id)) {
                ephemeralBuffers.delete(event.session_id);
                ephemeralBuffers = new Map(ephemeralBuffers);
              }
              if (ephemeralThinkingBuffers.has(event.session_id)) {
                ephemeralThinkingBuffers.delete(event.session_id);
                ephemeralThinkingBuffers = new Map(ephemeralThinkingBuffers);
              }
              lastPersistedSeq.set(event.session_id, Number.MAX_SAFE_INTEGER);
            }
          }
        }
      },
      (eph: EphemeralEvent) => {
        // Discard stale deltas (race: sync arrived before queued mid-turn POST)
        const persisted = lastPersistedSeq.get(eph.session_id) ?? 0;
        if (eph.msg_seq <= persisted) return;

        // Accumulate text from ephemeral delta
        const text = eph.payload.parts
          ?.filter((p: { kind: string }) => p.kind === 'text')
          .map((p: { kind: string; text?: string }) => p.text ?? '')
          .join('') ?? '';
        if (text) {
          const existing = ephemeralBuffers.get(eph.session_id);
          if (!existing || eph.msg_seq > existing.lastSeq) {
            ephemeralBuffers.set(eph.session_id, {
              text: (existing?.text ?? '') + text,
              lastSeq: eph.msg_seq,
            });
            ephemeralBuffers = new Map(ephemeralBuffers);
          }
        }

        // Accumulate thinking deltas from data parts
        const thinkingTexts = eph.payload.parts
          ?.filter((p: { kind: string; data?: unknown }) =>
            p.kind === 'data' &&
            (p.data as Record<string, unknown>)?.type === 'thinking_delta'
          )
          .map((p: { kind: string; data?: unknown }) =>
            ((p.data as Record<string, unknown>)?.thinking as string) ?? ''
          ) ?? [];
        const thinkingText = thinkingTexts.join('');
        if (thinkingText) {
          const existing = ephemeralThinkingBuffers.get(eph.session_id);
          if (!existing || eph.msg_seq > existing.lastSeq) {
            ephemeralThinkingBuffers.set(eph.session_id, {
              text: (existing?.text ?? '') + thinkingText,
              lastSeq: eph.msg_seq,
              startedAt: existing?.startedAt ?? new Date().toISOString(),
            });
            ephemeralThinkingBuffers = new Map(ephemeralThinkingBuffers);
          }
        }
      },
      () => { sseActive = true; sseReconnecting = false; },
      () => {
        sseActive = false;
        sseReconnecting = false;
      },
      () => { sseReconnecting = true; },
    );
    sseConnection = conn;

    return () => {
      conn.close();
      sseActive = false;
      sseReconnecting = false;
      sseConnection = null;
    };
  });

  // Events for the currently viewed session
  let activeSessionId = $derived(selectedSessionId ?? leadSession?.id ?? null);
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

  // Complete execution
  let showCompleteDialog = $state(false);
  let completeError: string | null = $state(null);

  async function handleComplete() {
    completeError = null;
    try {
      await completeMut.mutateAsync(executionId);
      showCompleteDialog = false;
    } catch (e) {
      completeError = e instanceof Error ? e.message : 'Failed to complete';
    }
  }

  // Recover execution (targets root lead session)
  let recoverError: string | null = $state(null);

  async function handleRecover() {
    if (!detail || !leadSession) return;
    recoverError = null;
    try {
      await recoverMut.mutateAsync({ sessionId: leadSession.id });
    } catch (e) {
      recoverError = e instanceof Error ? e.message : 'Recovery failed';
    }
  }

  // Re-run execution
  function handleRerun() {
    if (!detail) return;
    const exec = detail.execution;
    const pool = poolQuery.data ?? [];
    executionPrefill.set({
      sourceExecutionId: executionId,
      projectId: exec.project_id,
      agentId: leadSession?.agent_id,
      agentIds: pool.map(a => a.agent_id),
      prompt: exec.input,
      title: exec.title ? `Re-run: ${exec.title}` : undefined,
    });
    router.navigate('/executions/new');
  }

  // Completion summary helpers
  let terminalLabel = $derived(
    detail?.execution.status === 'completed' ? 'Completed at' :
    detail?.execution.status === 'failed' ? 'Failed at' :
    detail?.execution.status === 'canceled' ? 'Canceled at' : ''
  );
  let completionTime = $derived(detail?.execution.completed_at ?? detail?.execution.updated_at ?? null);

  // Copy-to-clipboard state for working directory
  let cwdCopied = $state(false);
  let cwdCopyTimeout: ReturnType<typeof setTimeout> | null = null;

  function copyWorkingDir() {
    const path = leadSession?.worktree_path;
    if (!path || !navigator.clipboard?.writeText) return;
    navigator.clipboard.writeText(path).then(() => {
      cwdCopied = true;
      if (cwdCopyTimeout) clearTimeout(cwdCopyTimeout);
      cwdCopyTimeout = setTimeout(() => { cwdCopied = false; }, 1500);
    }).catch(() => { /* clipboard not available */ });
  }
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
        <StatusBadge status={detail.execution.status} hasQuestions={$executionsWithQuestions.has(detail.execution.id) ? true : $noQuestionExecutions.has(detail.execution.id) ? false : detail.execution.status === 'input-required' ? undefined : false} />
        {#if isCancellable}
          <Button variant="destructive" size="sm" disabled={cancelMut.isPending} onclick={() => { cancelError = null; showCancelDialog = true; }}>
            {cancelMut.isPending ? 'Canceling...' : 'Cancel'}
          </Button>
        {/if}
        {#if isCompletable}
          <Button variant="outline" size="sm" disabled={completeMut.isPending} onclick={() => { completeError = null; showCompleteDialog = true; }}>
            {completeMut.isPending ? 'Completing...' : 'Complete'}
          </Button>
        {/if}
        {#if isRecoverable}
          <Button variant="secondary" size="sm" disabled={recoverMut.isPending} onclick={handleRecover}>
            {recoverMut.isPending ? 'Recovering...' : 'Attempt Recovery'}
          </Button>
        {/if}
        {#if isTerminal}
          <Button variant={isRecoverable ? 'outline' : 'secondary'} size="sm" disabled={!poolQuery.data} onclick={handleRerun}>
            Re-run
          </Button>
        {/if}
        {#if detail.execution.project_id}
          <Button variant="ghost" size="sm" onclick={() => { openSearchTab(detail!.execution.project_id!); router.navigate('#/wiki'); }}>
            Wiki
          </Button>
        {/if}
      </div>
      {#if recoverError}
        <div class="action-error">{recoverError}</div>
      {/if}
      <div class="detail-meta">
        {#if leadSession}
          <span>Agent: {agentName(leadSession.agent_id)}</span>
          <span class="meta-sep">&middot;</span>
        {/if}
        <ElapsedTime startTime={detail.execution.created_at} endTime={isTerminal ? (detail.execution.completed_at ?? detail.execution.updated_at) : null} />
        <span class="meta-sep">&middot;</span>
        <span>Depth: {detail.execution.max_depth}</span>
        <span class="meta-sep">&middot;</span>
        <span>Width: {detail.execution.max_width}</span>
        {#if leadSession?.worktree_path}
          <span class="meta-sep">&middot;</span>
          <span>Working Directory:</span>
          <button
            class="working-dir-btn"
            title="Copy working directory path"
            onclick={copyWorkingDir}
          >
            {cwdCopied ? 'Copied!' : leadSession.worktree_path}
          </button>
        {/if}
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

    {#if (poolQuery.data ?? []).length > 0}
      <div class="pool-section">
        <span class="pool-label">Agent Pool:</span>
        {#each poolQuery.data ?? [] as entry (entry.agent_id)}
          <span class="pool-chip">{entry.name}</span>
        {/each}
      </div>
    {/if}

    <QuestionBanner execution={detail.execution} sessions={detail.sessions} events={inputEvents} {agents} />

    {#if detail.sessions.length > 0}
      <SessionTree
        sessions={detail.sessions}
        {agents}
        {selectedSessionId}
        {isTerminal}
        onselectsession={handleSessionSelect}
        onstatuschange={() => {
          queryClient.invalidateQueries({ queryKey: ['execution', executionId] });
          queryClient.invalidateQueries({ queryKey: ['executions'] });
        }}
      />
    {/if}

    <div class="events-header">
      <span class="section-heading">Events</span>
      {#if !isTerminal}
        <span class="sse-indicator"
          class:connected={sseActive}
          class:reconnecting={sseReconnecting && !sseActive}
          class:disconnected={!sseActive && !sseReconnecting}
          title={sseActive ? 'Live (SSE)' : sseReconnecting ? 'Reconnecting...' : 'Disconnected'}
        >
          <span class="sse-dot"></span>
          <span class="sse-label">{sseActive ? 'Live' : sseReconnecting ? 'Reconnecting...' : 'Disconnected'}</span>
          {#if !sseActive && !sseReconnecting}
            <button class="sse-retry" onclick={() => sseConnection?.reconnect()}>Retry</button>
          {/if}
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
        <button
          class="toggle-btn"
          class:active={viewMode === 'diff'}
          role="tab"
          aria-selected={viewMode === 'diff'}
          onclick={() => viewMode = 'diff'}
        >Diff</button>
      </div>
    </div>

    {#if viewMode === 'log'}
      <EventsTimeline {events} {agents} sessions={detail.sessions} {eventFilter} onfilterchange={(f) => eventFilter = f} />
    {:else if viewMode === 'chat'}
      <ChatView {events} {agents} sessions={detail.sessions} sessionId={activeSessionId} ephemeralText={ephemeralBuffers.get(activeSessionId ?? '')?.text ?? ''} ephemeralThinking={ephemeralThinkingBuffers.get(activeSessionId ?? '') ?? null} settledThinkingDuration={settledThinkingDurations.get(activeSessionId ?? '') ?? null} {eventFilter} onfilterchange={(f) => eventFilter = f} />
    {:else if viewMode === 'diff'}
      <DiffPanel sessionId={activeSessionId} {isTerminal} />
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

  <AlertDialog.Root bind:open={showCompleteDialog}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay class="modal-overlay" />
      <AlertDialog.Content class="modal-content">
        <AlertDialog.Title class="modal-title">Complete Execution</AlertDialog.Title>
        <AlertDialog.Description class="modal-description">
          Mark this execution as complete? All active sessions will be stopped. This cannot be undone.
        </AlertDialog.Description>
        {#if completeError}
          <div class="modal-error">{completeError}</div>
        {/if}
        <div class="modal-actions">
          <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Keep Running</AlertDialog.Cancel>
          <button class="alert-btn alert-btn-primary" disabled={completeMut.isPending} onclick={handleComplete}>
            {completeMut.isPending ? 'Completing...' : 'Complete Execution'}
          </button>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  </AlertDialog.Root>
{/if}

<style>
  .detail-view {
    flex: 1;
    min-height: 0;
    min-width: 0;
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
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .meta-sep {
    opacity: 0.5;
  }

  .working-dir-btn {
    background: none;
    border: none;
    padding: 0;
    font: inherit;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    max-width: 20rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    direction: rtl;
    text-align: left;
  }

  .working-dir-btn:hover {
    color: hsl(var(--primary));
  }

  .completion-summary {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 1rem;
    margin: 0 1rem 0.25rem;
    border-radius: var(--radius);
    background: hsl(var(--muted) / 0.3);
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .summary-sep {
    opacity: 0.4;
  }

  .pool-section {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.375rem;
    padding: 0.375rem 1rem;
    font-size: 0.6875rem;
    flex-shrink: 0;
  }

  .pool-label {
    color: hsl(var(--muted-foreground));
    font-weight: 500;
  }

  .pool-chip {
    display: inline-block;
    padding: 0.0625rem 0.375rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--primary) / 0.1);
    color: hsl(var(--primary));
    font-size: 0.625rem;
    font-weight: 500;
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

  .sse-indicator.reconnecting {
    border-color: hsl(var(--status-attention) / 0.3);
    background: hsl(var(--status-attention) / 0.08);
  }

  .sse-indicator.reconnecting .sse-dot {
    background: hsl(var(--status-attention));
    animation: pulse 1.5s ease-in-out infinite;
  }

  .sse-indicator.reconnecting .sse-label {
    color: hsl(var(--status-attention));
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
  }

  .sse-retry {
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.625rem;
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
  }

  .sse-retry:hover {
    color: hsl(var(--primary));
  }

  .view-toggle {
    display: flex;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
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

  .action-error {
    padding: 0.375rem 1rem;
    font-size: 0.8125rem;
    color: hsl(var(--status-danger));
  }

  .modal-error {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
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
