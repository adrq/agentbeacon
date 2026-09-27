<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import type { Agent, AgentType, Event as BeaconEvent, EphemeralEvent, MessagePayload, UsageState } from '../types';
  import { isMessagePayload } from '../types';
  import { normalizeDataPart, type NormalizedToolCall } from '../normalize';
  import { api } from '../api';
  import { executionDetailQuery, sessionEventsQuery, terminateExecutionMutation, executionAgentsQuery, recoverSessionMutation, executionSessionsQuery, buildSessionIdentityMap, fetchSessionHistory, spliceSessionHistory, spliceSessionFullHistory } from '../queries/executions';
  import { agentsQuery } from '../queries/agents';
  import { useQueryClient } from '@tanstack/svelte-query';
  import { connectExecutionSSE, type SSEConnection } from '../sse';
  import { SSEBatcher } from '../sseBatch';
  import { isUnsupportedSchema } from '../eventSchema';
  import { clearHeldLiveEvents, holdLiveEvents } from '../liveEvents';
  import { readAfterRelease } from '../settlementRead';
  import { discardSharedRead, newSeamEpoch } from '../historyReads';
  import { deferTail, holdTail, releaseAllTails, releaseTail, tailHeld } from '../tailHold';
  import { onDestroy, untrack } from 'svelte';
  import StatusBadge from './StatusBadge.svelte';
  import QuestionBanner from './QuestionBanner.svelte';
  import EventsTimeline from './EventsTimeline.svelte';
  import ChatView from './ChatView.svelte';
  import ThreadView from './ThreadView.svelte';
  import DiffPanel from './DiffPanel.svelte';
  import ExecutionOrgChart from './ExecutionOrgChart.svelte';
  import SidebarSessionTree from './SidebarSessionTree.svelte';
  import { executionsWithQuestions } from '../stores/questionState';
  import Button from './ui/button.svelte';
  import { openSearchTab } from '../stores/wikiState.svelte';
  import { router } from '../router';
  import { executionPrefill, selectedSessionId, usageBySession } from '../stores/appState';
  import type { EventFilter } from '../eventFilterGroups';

  interface Props {
    executionId: string;
  }

  let { executionId }: Props = $props();

  const terminalOutcomes = new Set(['completed', 'failed', 'canceled']);

  const queryClient = useQueryClient();
  const agentsQ = agentsQuery();
  let agents = $derived<Agent[]>(agentsQ.data ?? []);

  const detailQuery = executionDetailQuery(() => executionId);
  const poolQuery = executionAgentsQuery(() => executionId);
  const sessionsQuery = executionSessionsQuery(() => executionId);
  let sessionIdentity = $derived(buildSessionIdentityMap(sessionsQuery.data ?? []));
  const terminateMut = terminateExecutionMutation();
  const recoverMut = recoverSessionMutation();

  let detail = $derived(detailQuery.data ?? null);
  let loading = $derived(detailQuery.isLoading);
  let error = $derived(detailQuery.error?.message ?? null);

  // Ephemeral streaming state (not in TanStack cache — transient)
  let ephemeralBuffers = $state<Map<string, { text: string; lastSeq: number }>>(new Map());
  let ephemeralThinkingBuffers = $state<Map<string, { text: string; lastSeq: number; startedAt: string }>>(new Map());
  // In-flight tool calls, from `item/started`. Codex only: it is the sole executor
  // whose start signal is ephemeral, so without this a Codex tool call is invisible
  // until it completes. Keyed session -> toolCallId; the persisted `item/completed`
  // clears the entry. Lost on disconnect like every other ephemeral, which is why
  // ChatView also shows a durable session-status indicator.
  let ephemeralToolCalls = $state<Map<string, Map<string, { call: NormalizedToolCall; startedAt: number }>>>(new Map());
  let settledThinkingDurations = $state<Map<string, { durationMs: number; startedAt: string }>>(new Map());
  let lastPersistedSeq = new Map<string, number>();
  // Running total of persisted text length per session, used to decide when
  // persisted content has caught up with the ephemeral buffer.
  let persistedTextLen = new Map<string, number>();

  // Event filter state (shared between Chat and Log views, resets on exec change)
  let eventFilter = $state<EventFilter>('all');

  // Org chart overview toggle
  let showOverview = $state(false);

  // Thread view state
  let threadTarget = $state<{ sessionA: string; sessionB: string } | null>(null);

  // Mobile overflow menu and details overlay
  let isMobile = $state(false);
  let overflowMenuOpen = $state(false);
  let showDetailsOverlay = $state(false);
  $effect(() => {
    const mql = window.matchMedia('(max-width: 768px)');
    isMobile = mql.matches;
    const handler = (e: MediaQueryListEvent) => { isMobile = e.matches; };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  });
  $effect(() => {
    if (!overflowMenuOpen) return;
    let handler: ((e: Event) => void) | null = null;
    const timerId = setTimeout(() => {
      handler = () => { overflowMenuOpen = false; };
      document.addEventListener('click', handler, { once: true });
    }, 0);
    return () => {
      clearTimeout(timerId);
      if (handler) document.removeEventListener('click', handler);
    };
  });

  // Debounced query invalidation — collapses rapid SSE state_change events
  // (e.g. a burst on page load) into a single batch of invalidations.
  let invalidateTimer: ReturnType<typeof setTimeout> | null = null;
  function debouncedInvalidate(execId: string) {
    if (invalidateTimer) clearTimeout(invalidateTimer);
    invalidateTimer = setTimeout(() => {
      invalidateTimer = null;
      queryClient.invalidateQueries({ queryKey: ['execution', execId] });
      queryClient.invalidateQueries({ queryKey: ['executions'] });
      queryClient.invalidateQueries({ queryKey: ['execution-sessions', execId] });
      queryClient.invalidateQueries({ queryKey: ['session-diff'] });
    }, 500);
  }

  // SSE connection state (declared before $effect.pre that references them)
  let sseActive = $state(false);
  // True once this connection's history repair has landed. Polling stops only
  // while it is true.
  let spliceSettled = $state(false);
  let spliceRetry: ReturnType<typeof setTimeout> | null = null;
  // The current repair generation, one per position event.
  let spliceGeneration = 0;

  // Starts a repair generation: cancels any pending retry, closes the gate,
  // and begins a new seam.
  function startRepairGeneration(): number {
    if (spliceRetry) { clearTimeout(spliceRetry); spliceRetry = null; }
    spliceSettled = false;
    newSeamEpoch();
    return ++spliceGeneration;
  }
  let sseReconnecting = $state(false);
  let sseConnection = $state<SSEConnection | null>(null);

  // The execution whose active-session history is cached. Set once per
  // execution, on the current session's successful load, and cleared on
  // execution change. Declared before the $effect.pre block that resets it.
  let initialLoadedExecId = $state<string | null>(null);

  // Sessions whose final history read has succeeded.
  // Declared here — before the reset block that clears them.
  let finalHistoryRead = $state(new Set<string>());
  // Bumped when a failed final read is due for another attempt.
  let finalReadRetry = $state(0);
  const finalReadInFlight = new Set<string>();

  // Event ids whose usage state has been applied, per session. Cleared on
  // execution change, and per session when that session's history is dropped.
  const processedUsageEventIds = new Map<string, Set<string>>();

  // Clear live-event holds on view destruction.
  onDestroy(clearHeldLiveEvents);
  onDestroy(releaseAllTails);

  // Drops a session's usage figures and processed ids when its history goes.
  const stopWatchingRemovals = queryClient.getQueryCache().subscribe(event => {
    if (event.type !== 'removed') return;
    const [name, id] = event.query.queryKey as [string, string];
    if (name !== 'session-events' || typeof id !== 'string') return;
    processedUsageEventIds.delete(id);
    const next = new Map($usageBySession);
    if (next.delete(id)) usageBySession.set(next);
  });
  onDestroy(stopWatchingRemovals);

  // Reset state when execution changes
  let prevExecId = '';
  $effect.pre(() => {
    if (executionId !== prevExecId) {
      prevExecId = executionId;
      selectedSessionId.set(null);
      usageBySession.set(new Map());
      processedUsageEventIds.clear();
      eventFilter = 'all';
      showOverview = false;
      threadTarget = null;
      overflowMenuOpen = false;
      showDetailsOverlay = false;
      const hashView = getHashViewParam();
      if (hashView) viewMode = hashView;
      lastPersistedSeq.clear();
      persistedTextLen.clear();
      // Clear live-event holds on execution change.
      clearHeldLiveEvents();
      releaseAllTails();
      ephemeralBuffers = new Map();
      ephemeralThinkingBuffers = new Map();
      ephemeralToolCalls = new Map();
      settledThinkingDurations = new Map();
      initialLoadedExecId = null;
      finalHistoryRead = new Set();
      finalReadInFlight.clear();
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

  // Initialize: URL hash param takes priority, then localStorage, then default 'chat'
  let hashMode = typeof window !== 'undefined' ? getHashViewParam() : null;
  let storedMode: string | null = null;
  try { storedMode = typeof window !== 'undefined' ? localStorage.getItem('agentbeacon-event-view-mode') : null; } catch { /* localStorage unavailable */ }
  let viewMode = $state<ViewMode>(
    hashMode ?? (storedMode && validModes.has(storedMode) ? storedMode as ViewMode : 'chat')
  );

  $effect(() => {
    try { if (typeof window !== 'undefined') localStorage.setItem('agentbeacon-event-view-mode', viewMode); } catch { /* localStorage unavailable */ }
    setHashViewParam(viewMode);
  });

  let leadSession = $derived(detail?.sessions.find(s => !s.parent_session_id) ?? null);
  let displayTitle = $derived(detail?.execution.title ?? executionId.slice(0, 8));
  let isTerminal = $derived(
    (detail?.execution.outcome != null) || (detail?.execution.desired === 'terminate')
  );
  // Settled once all sessions have an outcome.
  let isSettled = $derived(
    isTerminal && (detail?.sessions.every(s => s.outcome != null) ?? false)
  );
  let isTerminable = $derived(!isTerminal);
  let isCompletionEligible = $derived(detail?.execution.completion_eligible ?? false);
  let isRecoverable = $derived(
    detail?.execution.outcome === 'failed' && leadSession?.outcome === 'failed' && leadSession?.agent_session_id != null
  );

  // Auto-select lead session when first opening an execution
  $effect(() => {
    const lead = leadSession;
    if (lead && $selectedSessionId === null) {
      selectedSessionId.set(lead.id);
    }
  });

  // Reset thread view when selected session changes
  $effect(() => {
    $selectedSessionId;
    threadTarget = null;
  });

  // Helper: resolve agent type for a session.
  // Prefer poolQuery (execution-scoped, loaded early) over the global agents array
  // to avoid the race where agentsQuery hasn't settled yet when SSE events arrive.
  function agentTypeForSession(sessionId: string): AgentType {
    const session = detail?.sessions.find(s => s.id === sessionId);
    if (!session) return 'claude_sdk';
    const pool = poolQuery.data;
    const poolAgent = pool?.find(a => a.agent_id === session.agent_id);
    if (poolAgent) return (poolAgent.agent_type as AgentType) ?? 'claude_sdk';
    const globalAgent = agents.find(a => a.id === session.agent_id);
    return (globalAgent?.agent_type as AgentType) ?? 'claude_sdk';
  }

  function emptyUsage(): UsageState {
    return {
      usedTokens: 0, inputTokens: 0, outputTokens: 0, contextWindow: 0,
      compactions: 0, available: false, supportsContextPercentage: false,
    };
  }

  /**
   * Accumulate usage state for one event into a working map. Does not touch
   * the store: callers replay events into a local copy and publish once via
   * publishUsage, so intermediate per-event values never hit the store.
   * Returns whether the event wrote anything into the working map.
   *
   * Token and context figures are re-derived by replaying the window in event
   * order. Compaction counts are per-event, guarded by the processed-id set.
   */
  function accumulateUsageFromEvent(next: Map<string, UsageState>, event: BeaconEvent): boolean {
    if (isUnsupportedSchema(event)) return false;
    if (event.event_type !== 'message' || !event.session_id) return false;
    let seen = processedUsageEventIds.get(event.session_id);
    if (!seen) {
      seen = new Set<string>();
      processedUsageEventIds.set(event.session_id, seen);
    }
    const counted = seen.has(event.id);
    seen.add(event.id);
    const at = agentTypeForSession(event.session_id);
    const payload = event.payload as MessagePayload;
    let touched = false;
    for (const part of payload.parts ?? []) {
      if (!('data' in part)) continue;
      const d = (part as { data: unknown }).data;
      if (typeof d !== 'object' || d === null) continue;
      const dataObj = d as { type?: string; method?: string; tokenUsage?: unknown; [key: string]: unknown };
      if (!dataObj.type && !dataObj.tokenUsage && !dataObj.method) continue;
      const norm = normalizeDataPart(at, dataObj as Record<string, unknown>);
      if (norm.normalized === 'usage') {
        const current = next.get(event.session_id) ?? emptyUsage();
        next.set(event.session_id, {
          ...current,
          // Use || not ?? — Claude's usage_snapshot sends input_tokens: 0 meaning
          // "not populated", not "zero tokens". Treating 0 as falsy is intentional.
          usedTokens: norm.usedTokens || current.usedTokens,
          inputTokens: norm.inputTokens || current.inputTokens,
          outputTokens: norm.outputTokens || current.outputTokens,
          contextWindow: norm.modelContextWindow ?? current.contextWindow,
        });
        touched = true;
      } else if (norm.normalized === 'compaction') {
        if (counted) continue;
        const current = next.get(event.session_id) ?? emptyUsage();
        next.set(event.session_id, {
          ...current,
          compactions: current.compactions + 1,
        });
        touched = true;
      }
    }
    return touched;
  }

  function usageEntriesEqual(a: UsageState, b: UsageState): boolean {
    return a.usedTokens === b.usedTokens
      && a.inputTokens === b.inputTokens
      && a.outputTokens === b.outputTokens
      && a.contextWindow === b.contextWindow
      && a.compactions === b.compactions
      && a.available === b.available
      && a.supportsContextPercentage === b.supportsContextPercentage;
  }

  // Publish a replayed usage map only when it differs from the store. The
  // replaying $effect reads the store, so a value-identical write with a
  // fresh Map identity re-triggers it and loops until Svelte's update-depth
  // guard aborts the reactive flush (without running effect cleanups).
  function publishUsage(next: Map<string, UsageState>) {
    const current = $usageBySession;
    if (next.size === current.size) {
      let equal = true;
      for (const [id, entry] of next) {
        const existing = current.get(id);
        if (!existing || !usageEntriesEqual(existing, entry)) { equal = false; break; }
      }
      if (equal) return;
    }
    usageBySession.set(next);
  }

  // Single-event entry point for the SSE delivery path. Screens out
  // non-message deliveries before paying for the map copy, and skips the
  // publish diff when the event contributed nothing.
  function applyUsageFromEvent(event: BeaconEvent) {
    if (event.event_type !== 'message' || !event.session_id) return;
    const next = new Map($usageBySession);
    if (accumulateUsageFromEvent(next, event)) publishUsage(next);
  }

  // Mark the execution's initial history as loaded (see initialLoadedExecId).
  $effect(() => {
    if (eventsQuery.isSuccess && activeSessionId && initialLoadedExecId !== executionId) {
      initialLoadedExecId = executionId;
    }
  });

  // Writes live rows to whichever of a session's caches exist, holding them
  // when neither does or one is still being built.
  function appendLiveRows(key: string, evs: BeaconEvent[]) {
    const append = (queryKey: unknown[]) => {
      queryClient.setQueryData(queryKey, (old: BeaconEvent[] | undefined) => {
        if (!old) return old;
        const ids = new Set(old.map(e => e.id));
        const add = evs.filter(e => !ids.has(e.id));
        return add.length ? [...old, ...add] : old;
      });
    };
    const windowKey = ['session-events', key];
    const fullKey = ['session-events-full', key];
    const hasWindow = !!queryClient.getQueryData(windowKey);
    const hasFull = !!queryClient.getQueryData(fullKey);
    const building = (queryKey: unknown[]) =>
      !!queryClient.getQueryState(queryKey) && queryClient.getQueryData(queryKey) === undefined;
    if ((!hasWindow && !hasFull) || building(windowKey) || building(fullKey)) {
      holdLiveEvents(key, evs);
    }
    if (hasWindow) append(windowKey);
    if (hasFull) append(fullKey);
  }

  // Bumped on every release, for work a hold skipped.
  let tailReleases = $state(0);

  // Holds and releases writes to the end of one session's history.
  function setTailFence(id: string, held: boolean) {
    if (held) {
      holdTail(id);
      return;
    }
    releaseTail(id);
    tailReleases += 1;
  }

  // rAF-batched flush with a latency watchdog so background tabs (throttled
  // rAF) and sparse delivery still flush promptly.
  function createFlushScheduler() {
    let raf = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    return {
      request(flush: () => void) {
        if (!raf) raf = requestAnimationFrame(() => { raf = 0; flush(); });
        if (timer === undefined) timer = setTimeout(() => { timer = undefined; flush(); }, 32);
      },
      cancel() {
        if (raf) { cancelAnimationFrame(raf); raf = 0; }
        if (timer !== undefined) { clearTimeout(timer); timer = undefined; }
      },
    };
  }

  // Clears buffered ephemeral state, then refetches history from
  // `historyBefore` for every session with a cached history here. Returns
  // whether every one landed.
  async function resplice(historyBefore: string | null, generation: number): Promise<boolean> {
    clearEphemeralState();
    const ids = new Set<string>();
    if (activeSessionId) ids.add(activeSessionId);
    for (const session of detail?.sessions ?? []) {
      if (queryClient.getQueryData(['session-events', session.id])) ids.add(session.id);
    }
    // Whole-history caches are repaired here too, including ones being built.
    const fullIds = new Set<string>();
    for (const session of detail?.sessions ?? []) {
      if (queryClient.getQueryState(['session-events-full', session.id])) fullIds.add(session.id);
    }
    const results = await Promise.all([
      ...[...ids].map(id =>
        spliceSessionHistory(queryClient, id, historyBefore, {
          isCurrent: () => generation === spliceGeneration,
        }).then(
          () => true,
          () => false,
        ),
      ),
      ...[...fullIds].map(id =>
        spliceSessionFullHistory(queryClient, id, historyBefore, {
          isCurrent: () => generation === spliceGeneration,
        }).then(
          () => true,
          () => false,
        ),
      ),
    ]);
    return results.every(Boolean);
  }

  // Repairs history for one seam, with bounded retries.
  const SPLICE_RETRY_DELAYS_MS = [500, 1000, 2000, 4000, 8000];
  async function repairHistory(historyBefore: string | null, generation: number, attempt = 0) {
    const settled = await resplice(historyBefore, generation);
    // A superseded generation settles nothing and schedules nothing.
    if (generation !== spliceGeneration) return;
    if (settled) {
      spliceSettled = true;
      return;
    }
    const delay = SPLICE_RETRY_DELAYS_MS[Math.min(attempt, SPLICE_RETRY_DELAYS_MS.length - 1)];
    spliceRetry = setTimeout(() => {
      if (generation !== spliceGeneration) return;
      void repairHistory(historyBefore, generation, attempt + 1);
    }, delay);
  }

  // Set while no stream is connected, cleared by the next position event.
  let ephemeralsGated = $state(false);

  function clearEphemeralState() {
    ephemeralBuffers = new Map();
    ephemeralThinkingBuffers = new Map();
    ephemeralToolCalls = new Map();
    settledThinkingDurations = new Map();
    lastPersistedSeq.clear();
    persistedTextLen.clear();
  }

  // SSE connection lifecycle — stay live until tree is fully settled
  $effect(() => {
    const execId = executionId;
    const settled = isSettled;
    const stillLoading = detailQuery.isLoading;
    if (settled || stillLoading || poolQuery.isLoading || initialLoadedExecId !== executionId) {
      sseActive = false;
      return;
    }

    // Connection-local batcher: buffers cache writes and flushes them in one
    // write per session. Created inside the effect so it cannot outlive the
    // connection across execution switches.
    const scheduler = createFlushScheduler();
    const batcher = new SSEBatcher<BeaconEvent>(
      {
        getExisting: (key: string) => queryClient.getQueryData<BeaconEvent[]>(['session-events', key]),
        appendNew: (key: string, evs) => {
          if (tailHeld(key)) {
            deferTail(key, () => appendLiveRows(key, evs));
            return;
          }
          appendLiveRows(key, evs);
        },
      },
      scheduler,
    );

    const conn = connectExecutionSSE(
      execId,
      (event: BeaconEvent) => {
        // Runs on every delivery; it de-dupes per event itself.
        applyUsageFromEvent(event);

        // Enqueue newly observed events; side effects run on first delivery only.
        const isNewEvent = batcher.enqueue(event);
        if (!isNewEvent) return;

        // Judged from the envelope alone, so an unreadable payload still drives
        // the refresh that settles the stream.
        if (event.event_type === 'state_change' && event.session_id === null) {
          debouncedInvalidate(execId);
        }

        // The row still reaches the cache and renders generically, but nothing
        // below reads inside a payload this client cannot parse.
        if (isUnsupportedSchema(event)) return;

        if (event.event_type === 'message' && event.session_id) {
          lastPersistedSeq.set(event.session_id, Math.max(
            lastPersistedSeq.get(event.session_id) ?? 0,
            event.msg_seq ?? 0,
          ));
          const payload = event.payload as MessagePayload;
          const persistedText = payload.parts
            ?.filter((p: Record<string, unknown>) => 'text' in p)
            .map((p: Record<string, unknown>) => (p.text as string) ?? '')
            .join('') ?? '';
          if (event.truncated) {
            // Text length cannot be compared when the text was shortened.
            ephemeralBuffers.delete(event.session_id);
            persistedTextLen.delete(event.session_id);
            ephemeralBuffers = new Map(ephemeralBuffers);
          } else if (persistedText) {
            const buf = ephemeralBuffers.get(event.session_id);
            // Only accumulate and compare when the persisted event is for
            // the message currently being streamed. Late arrivals from
            // earlier msg_seqs must not inflate the counter.
            if (buf && (event.msg_seq ?? 0) >= buf.lastSeq) {
              const prevLen = persistedTextLen.get(event.session_id) ?? 0;
              const totalLen = prevLen + persistedText.length;
              persistedTextLen.set(event.session_id, totalLen);
              if (totalLen >= buf.text.length) {
                ephemeralBuffers.delete(event.session_id);
                persistedTextLen.delete(event.session_id);
                ephemeralBuffers = new Map(ephemeralBuffers);
              }
            }
          }
          const thinkBuf = ephemeralThinkingBuffers.get(event.session_id);
          if (thinkBuf && (event.msg_seq ?? 0) >= thinkBuf.lastSeq) {
            const hasPersistedThinking = payload.parts?.some(
              (p: Record<string, unknown>) => {
                if (!('data' in p)) return false;
                const d = p.data as Record<string, unknown>;
                const dt = d?.type;
                // Claude: thinking (persisted); Codex legacy: reasoning (type-based)
                if (dt === 'thinking' || dt === 'reasoning') return true;
                // Codex new shape: full notification with method + params.item
                const method = d?.method as string | undefined;
                if (method === 'item/completed' || method === 'item/started') {
                  const item = (d?.params as Record<string, unknown>)?.item as Record<string, unknown> | undefined;
                  if (item?.type === 'reasoning') return true;
                }
                return false;
              }
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

          // Settle Codex ephemeral text buffer when persisted agentMessage
          // arrives. Usage/compaction accumulation is handled by
          // applyUsageFromEvent (called above, pre-isNewEvent gate).
          const at = agentTypeForSession(event.session_id);
          for (const part of payload.parts ?? []) {
            if (!('data' in part)) continue;
            const d = (part as { data: unknown }).data;
            if (typeof d !== 'object' || d === null) continue;
            const dataObj = d as { type?: string; method?: string; tokenUsage?: unknown; [key: string]: unknown };
            if (!dataObj.type && !dataObj.tokenUsage && !dataObj.method) continue;

            const norm = normalizeDataPart(at, dataObj as Record<string, unknown>);
            // The persisted completion supersedes the in-flight row.
            if (norm.normalized === 'tool_call' && norm.toolCallId) {
              const bySession = ephemeralToolCalls.get(event.session_id);
              if (bySession?.has(norm.toolCallId)) {
                const next = new Map(bySession);
                next.delete(norm.toolCallId);
                ephemeralToolCalls = new Map(ephemeralToolCalls).set(event.session_id, next);
              }
            }
            if (norm.normalized === 'text') {
              // Persisted Codex agentMessage → settle the ephemeral text buffer
              const buf = ephemeralBuffers.get(event.session_id);
              if (buf && (event.msg_seq ?? 0) >= buf.lastSeq) {
                ephemeralBuffers.delete(event.session_id);
                persistedTextLen.delete(event.session_id);
                ephemeralBuffers = new Map(ephemeralBuffers);
              }
            }
          }
        }

        if (event.event_type === 'state_change') {
          debouncedInvalidate(execId);
          if (event.session_id) {
            const p = event.payload as { executor_state?: string; outcome?: string; to?: string };
            // New format: executor_state; Legacy format: to
            const isRunning = p.executor_state === 'running' || p.to === 'working';
            const isTerminalEvent = p.outcome != null;
            if (isRunning) {
              lastPersistedSeq.delete(event.session_id);
              persistedTextLen.delete(event.session_id);
              settledThinkingDurations.delete(event.session_id);
              settledThinkingDurations = new Map(settledThinkingDurations);
              ephemeralThinkingBuffers.delete(event.session_id);
              ephemeralThinkingBuffers = new Map(ephemeralThinkingBuffers);
            } else if (p.executor_state === 'idle' || p.executor_state === 'crashed' || isTerminalEvent) {
              if (ephemeralBuffers.has(event.session_id)) {
                ephemeralBuffers.delete(event.session_id);
                persistedTextLen.delete(event.session_id);
                ephemeralBuffers = new Map(ephemeralBuffers);
              }
              if (ephemeralThinkingBuffers.has(event.session_id)) {
                ephemeralThinkingBuffers.delete(event.session_id);
                ephemeralThinkingBuffers = new Map(ephemeralThinkingBuffers);
              }
              // Nothing can still be running once the session has stopped, so
              // drop started-but-never-completed rows rather than spinning forever.
              if (ephemeralToolCalls.has(event.session_id)) {
                const next = new Map(ephemeralToolCalls);
                next.delete(event.session_id);
                ephemeralToolCalls = next;
              }
              lastPersistedSeq.set(event.session_id, Number.MAX_SAFE_INTEGER);
            }
          }
        }
      },
      (eph: EphemeralEvent) => {
        // Deltas are lossy by contract: none before the repair lands, none
        // after the stream ends.
        if (!spliceSettled || ephemeralsGated) return;
        const persisted = lastPersistedSeq.get(eph.session_id) ?? 0;
        if (eph.msg_seq <= persisted) return;

        const text = eph.payload.parts
          ?.filter((p: Record<string, unknown>) => 'text' in p)
          .map((p: Record<string, unknown>) => (p.text as string) ?? '')
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

        // `item/started` is the only signal that a Codex tool call has begun;
        // it is never persisted, so without this the call is invisible until it
        // completes. The same item id arrives later on `item/completed`, which
        // clears the entry — see the settle pass over persisted events.
        for (const part of eph.payload.parts ?? []) {
          if (!('data' in part)) continue;
          const d = (part as { data: unknown }).data;
          if (typeof d !== 'object' || d === null) continue;
          const raw = d as Record<string, unknown>;
          if (raw.method !== 'item/started') continue;
          const norm = normalizeDataPart(agentTypeForSession(eph.session_id), raw);
          if (norm.normalized !== 'tool_call' || !norm.toolCallId) continue;
          const startedAtMs = (raw.params as Record<string, unknown> | undefined)?.startedAtMs;
          const bySession = new Map(ephemeralToolCalls.get(eph.session_id) ?? []);
          bySession.set(norm.toolCallId, {
            call: norm,
            startedAt: typeof startedAtMs === 'number' ? startedAtMs : Date.now(),
          });
          ephemeralToolCalls = new Map(ephemeralToolCalls).set(eph.session_id, bySession);
        }

        const thinkingTexts = eph.payload.parts
          ?.filter((p: Record<string, unknown>) => {
            if (!('data' in p)) return false;
            const d = p.data as Record<string, unknown>;
            const dt = d?.type;
            // Claude: thinking_delta; Codex legacy: reasoning (type-based)
            if (dt === 'thinking_delta' || dt === 'reasoning') return true;
            // Codex new shape: full notification with method field for reasoning deltas
            const method = d?.method as string | undefined;
            return method != null && method.includes('reasoning');
          })
          .map((p: Record<string, unknown>) => {
            const d = p.data as Record<string, unknown>;
            // Claude puts text in `thinking`; Codex legacy reasoning deltas carry `text`
            if (d.thinking) return (d.thinking as string);
            if (d.text) return (d.text as string);
            // Codex new shape: delta may be a plain string or object with .text
            const params = d.params as Record<string, unknown> | undefined;
            const delta = params?.delta;
            return typeof delta === 'string' ? delta : (delta as any)?.text ?? '';
          }) ?? [];
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
      () => {
        sseActive = true;
        sseReconnecting = false;
      },
      () => {
        // Flush buffered writes before the cursor may advance on reconnect.
        batcher.flush();
        sseActive = false;
        sseReconnecting = false;
      },
      () => {
        batcher.flush();
        sseReconnecting = true;
      },
      (position) => {
        ephemeralsGated = false;
        void repairHistory(position.history_before, startRepairGeneration());
      },
      (problem) => {
        console.warn('[SSE] protocol error', problem.code);
      },
      () => {
        // No stream is left to clear this state, so it goes now.
        ephemeralsGated = true;
        clearEphemeralState();
      },
    );
    sseConnection = conn;

    return () => {
      conn.close();
      batcher.dispose();
      sseActive = false;
      startRepairGeneration();
      sseReconnecting = false;
      sseConnection = null;
      if (invalidateTimer) { clearTimeout(invalidateTimer); invalidateTimer = null; }
    };
  });

  // Seed usage flags from session/agent data
  $effect(() => {
    const sessions = detail?.sessions;
    if (!sessions) return;

    let changed = false;
    const next = new Map($usageBySession);
    for (const s of sessions) {
      const poolAgent = poolQuery.data?.find(a => a.agent_id === s.agent_id);
      const globalAgent = !poolAgent ? agents.find(a => a.id === s.agent_id) : undefined;
      const at = poolAgent?.agent_type ?? globalAgent?.agent_type;
      // has_usage_metrics: enables the usage widget with raw token counts
      const available = at === 'claude_sdk' || at === 'codex_sdk';
      // supports_context_percentage: enables the fill bar when the executor reports a context window
      const supportsContextPercentage = at === 'claude_sdk' || at === 'codex_sdk';
      const existing = next.get(s.id);
      if (!existing) {
        next.set(s.id, {
          usedTokens: 0, inputTokens: 0, outputTokens: 0, contextWindow: 0,
          compactions: 0, available, supportsContextPercentage,
        });
        changed = true;
      } else if (existing.available !== available || existing.supportsContextPercentage !== supportsContextPercentage) {
        next.set(s.id, { ...existing, available, supportsContextPercentage });
        changed = true;
      }
    }
    if (changed) usageBySession.set(next);
  });

  // Events for the currently viewed session
  let activeSessionId = $derived($selectedSessionId ?? leadSession?.id ?? null);
  // Whether the viewed session is finished (outcome set). Scoped to the
  // single session, not execution-wide, so orphaned tool_results surface
  // correctly even while sibling sessions are still running.

  let viewedSessionSettled = $derived.by(() => {
    const s = detail?.sessions.find(s => s.id === activeSessionId);
    return s ? (s.outcome != null && finalHistoryRead.has(s.id)) : false;
  });
  const eventsQuery = sessionEventsQuery(
    () => activeSessionId,
    () => viewedSessionSettled,
    () => sseActive && spliceSettled,
  );
  let events = $derived(eventsQuery.data ?? []);

  // A session is released when its outcome is set and no worker or command
  // remains.
  const FINAL_READ_RETRY_MS = 3000;
  const sessionReleased = (s: {
    outcome: string | null;
    worker_id: string | null;
    command_type: string | null;
  }) => s.outcome != null && s.worker_id == null && s.command_type == null;

  // Every mark comes from a read started after the session was seen released.
  $effect(() => {
    finalReadRetry;
    // A thread opened after release creates a history that nothing has proved
    // yet, so this runs again when one appears.
    threadTarget;
    const viewed = activeSessionId;
    const execId = executionId;
    for (const s of detail?.sessions ?? []) {
      if (!sessionReleased(s)) continue;
      const id = s.id;
      if (finalHistoryRead.has(id) || finalReadInFlight.has(id)) continue;
      // Either cache may hold this session.
      const keys = [['session-events', id], ['session-events-full', id]]
        .filter(key => queryClient.getQueryState<Event[]>(key));
      // A history never fetched here is read when the session is opened.
      if (id !== viewed && keys.length === 0) continue;

      finalReadInFlight.add(id);
      // Forgotten so this read is not answered by one begun earlier.
      discardSharedRead(id);
      void Promise.all(
        (keys.length ? keys : [['session-events', id]]).map(key =>
          readAfterRelease(queryClient, key),
        ),
      )
        .then(proofs => {
          if (executionId !== execId) {
            finalReadInFlight.delete(id);
            return;
          }
          // Not marked unless every key reported a read.
          if (!proofs.every(Boolean)) {
            setTimeout(() => {
              finalReadInFlight.delete(id);
              finalReadRetry += 1;
            }, FINAL_READ_RETRY_MS);
            return;
          }
          finalReadInFlight.delete(id);
          finalHistoryRead = new Set(finalHistoryRead).add(id);
        })
        .catch(() => {
          setTimeout(() => {
            finalReadInFlight.delete(id);
            finalReadRetry += 1;
          }, FINAL_READ_RETRY_MS);
        });
    }
  });


  // Events-panel loading placeholder (Log + Chat). Shown only on the first open
  // of a session this visit (no cached events) with a large history, where the
  // synchronous transform would otherwise freeze the panel. Phases: fetch (no
  // data yet) → prepare (count shown, transform deferred one painted frame) →
  // mounting (view built underneath, placeholder held until its first scrolled
  // frame) → live (view only).
  type PanelPhase = 'fetch' | 'prepare' | 'mounting' | 'live';
  const PREPARE_EVENT_THRESHOLD = 2000;
  let panelPhase = $state<PanelPhase>('live');
  let pendingCount = $state(0);
  let prepareRaf = 0;
  let showEventsPanelView = $derived(panelPhase === 'mounting' || panelPhase === 'live');

  function onPanelReady() {
    if (panelPhase === 'mounting') panelPhase = 'live';
  }

  // Pick the phase when the viewed session changes: cached history mounts
  // instantly; otherwise start in the fetch phase.
  $effect(() => {
    const id = activeSessionId;
    if (prepareRaf) { cancelAnimationFrame(prepareRaf); prepareRaf = 0; }
    if (!id) { panelPhase = 'live'; return; }
    const cached = untrack(() => queryClient.getQueryData<BeaconEvent[]>(['session-events', id]));
    panelPhase = cached && cached.length ? 'live' : 'fetch';
  });

  // When the fetch resolves: small histories mount synchronously (no blink);
  // large ones show the count, then defer the transform past a painted frame
  // (double rAF) so the placeholder is actually visible.
  $effect(() => {
    const n = events.length;
    if (untrack(() => panelPhase) !== 'fetch' || !eventsQuery.isSuccess) return;
    if (n > PREPARE_EVENT_THRESHOLD) {
      pendingCount = n;
      panelPhase = 'prepare';
      prepareRaf = requestAnimationFrame(() => {
        prepareRaf = requestAnimationFrame(() => {
          prepareRaf = 0;
          if (untrack(() => panelPhase) === 'prepare') panelPhase = 'mounting';
        });
      });
    } else {
      panelPhase = 'live';
    }
  });

  $effect(() => () => { if (prepareRaf) cancelAnimationFrame(prepareRaf); });

  // Drive usage/compaction accumulation from the polling cache so the
  // context indicator works even when SSE is unavailable (permanent
  // fallback after MAX_CONSECUTIVE_ERRORS, terminal executions where SSE
  // never attaches, or mid-lifecycle executions where polling delivers
  // events before the stream attaches). The per-event dedupe set
  // guarantees each event contributes exactly once regardless of path.
  // Wait for execution + pool data to resolve so agent-type lookup doesn't
  // fall back to claude_sdk for Codex sessions, permanently mis-normalizing
  // the initial event batch.
  $effect(() => {
    if (detailQuery.isLoading || poolQuery.isLoading) return;
    // Re-runs on release.
    tailReleases;
    // Skipped while the session is held; the release brings this round again.
    if (activeSessionId && tailHeld(activeSessionId)) return;
    // Replayed in event order on every pass, into a working copy; a single
    // publish at the end keeps intermediate per-event values (token counts
    // are not monotonic across a window) out of the store.
    const next = new Map($usageBySession);
    let touched = false;
    for (const event of events) {
      if (accumulateUsageFromEvent(next, event)) touched = true;
    }
    if (touched) publishUsage(next);
  });

  // Usage/compaction extraction lives in accumulateUsageFromEvent, reached
  // from the SSE callback (via applyUsageFromEvent) and the polling fallback
  // $effect (above). The per-event dedupe set guarantees each compaction
  // contributes exactly once regardless of delivery path; usage figures are
  // replayed and converge to the same values on every pass.

  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
  }

  // Per-session settled check for the thread view's two history queries.
  function sessionSettled(id: string): boolean {
    return (
      detail?.sessions.find(s => s.id === id)?.outcome != null && finalHistoryRead.has(id)
    );
  }

  // Terminate execution (covers both cancel and complete)
  let showTerminateDialog = $state(false);
  let terminateError: string | null = $state(null);

  async function handleTerminate() {
    terminateError = null;
    try {
      await terminateMut.mutateAsync(executionId);
      showTerminateDialog = false;
    } catch (e) {
      terminateError = e instanceof Error ? e.message : 'Failed to terminate';
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
  async function handleRerun() {
    if (!detail) return;
    const exec = detail.execution;
    const pool = poolQuery.data ?? [];

    let promptText = '';
    const rootSession = detail.sessions.find(s => !s.parent_session_id);
    if (rootSession) {
      try {
        const history = await fetchSessionHistory(rootSession.id);
        let firstMsg = history.find(e =>
          e.event_type === 'message' && isMessagePayload(e.payload) && e.payload.role === 'ROLE_USER'
        );
        // Fetch the whole row before copying its text.
        if (firstMsg?.truncated) {
          firstMsg = await api.getExecutionEvent(firstMsg.execution_id, firstMsg.id);
        }
        if (firstMsg && isMessagePayload(firstMsg.payload)) {
          const textParts = firstMsg.payload.parts
            ?.filter((p: import('../types').MessagePart) => 'text' in p)
            ?.map((p: any) => p.text) ?? [];
          promptText = textParts.join('\n');
        }
      } catch {
        // Cannot recover original prompt
      }
    }

    executionPrefill.set({
      sourceExecutionId: executionId,
      projectId: exec.project_id,
      agentId: leadSession?.agent_id,
      agentIds: pool.map(a => a.agent_id),
      prompt: promptText,
      title: exec.title ? `Re-run: ${exec.title}` : undefined,
    });
    router.navigate('/executions/new');
  }
</script>

{#if loading}
  <div class="detail-loading">Loading execution...</div>
{:else if error}
  <div class="detail-error">{error}</div>
{:else if detail}
  <div class="detail-view scroll-thin">
    <div class="detail-header">
      <h2 class="detail-title">{displayTitle}</h2>
      <StatusBadge status={detail.execution.status} hasQuestions={$executionsWithQuestions.has(detail.execution.id)} />
      {#if isTerminable}
        <Button variant={isCompletionEligible ? 'outline' : 'destructive'} size="sm" disabled={terminateMut.isPending} onclick={() => { terminateError = null; showTerminateDialog = true; }}>
          {terminateMut.isPending ? 'Terminating...' : isCompletionEligible ? 'Complete' : 'Cancel'}
        </Button>
      {/if}
      <span class="desktop-only-actions">
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
        {#if (detail?.sessions.length ?? 0) > 1}
          <Button class="org-chart-toggle" variant={showOverview ? 'default' : 'ghost'} size="sm" aria-pressed={showOverview} aria-controls="execution-overview" onclick={() => { showOverview = !showOverview; }}>
            Overview
          </Button>
        {/if}
        {#if detail.execution.project_id}
          <Button variant="ghost" size="sm" onclick={() => { openSearchTab(detail!.execution.project_id!); router.navigate('#/wiki'); }}>
            Wiki
          </Button>
        {/if}
      </span>
      {#if isMobile}
        <div class="overflow-menu-wrapper">
          <button class="overflow-menu-btn" aria-label="More actions" onclick={(e) => { e.stopPropagation(); overflowMenuOpen = !overflowMenuOpen; }}>⋯</button>
          {#if overflowMenuOpen}
            <div class="overflow-menu">
              {#if isRecoverable}
                <button class="overflow-item" disabled={recoverMut.isPending} onclick={() => { overflowMenuOpen = false; handleRecover(); }}>{recoverMut.isPending ? 'Recovering...' : 'Attempt Recovery'}</button>
              {/if}
              {#if isTerminal}
                <button class="overflow-item" disabled={!poolQuery.data} onclick={() => { overflowMenuOpen = false; handleRerun(); }}>Re-run</button>
              {/if}
              {#if (detail?.sessions.length ?? 0) > 1}
                <button class="overflow-item" onclick={() => { overflowMenuOpen = false; showOverview = !showOverview; }}>Overview</button>
              {/if}
              {#if detail.execution.project_id}
                <button class="overflow-item" onclick={() => { overflowMenuOpen = false; openSearchTab(detail!.execution.project_id!); router.navigate('#/wiki'); }}>Wiki</button>
              {/if}
              <button class="overflow-item" onclick={() => { overflowMenuOpen = false; showDetailsOverlay = true; }}>Execution Details</button>
            </div>
          {/if}
        </div>
      {/if}
    </div>
    {#if recoverError}
      <div class="action-error">{recoverError}</div>
    {/if}

    <QuestionBanner execution={detail.execution} />

    {#if showOverview}
      <ExecutionOrgChart
        sessions={detail.sessions}
        {agents}
        {sessionIdentity}
        onselectsession={(id) => {
          selectedSessionId.set(id);
          showOverview = false;
        }}
        onstatuschange={() => queryClient.invalidateQueries({ queryKey: ['execution', executionId] })}
      />
    {:else}
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
        {#if !threadTarget}
          <div class="view-toggle-wrapper">
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
        {/if}
      </div>

      {#if threadTarget}
        <ThreadView
          sessionA={threadTarget.sessionA}
          sessionB={threadTarget.sessionB}
          {sessionIdentity}
          {sessionSettled}
          sseActive={sseActive && spliceSettled}
          onclose={() => { threadTarget = null; }}
        />
      {:else if viewMode === 'diff'}
        <DiffPanel sessionId={activeSessionId} {isTerminal} />
      {:else}
        <div class="events-panel">
          {#if showEventsPanelView}
            {#if viewMode === 'log'}
              <EventsTimeline {events} {agents} sessions={detail.sessions} sessionId={activeSessionId} agentPool={poolQuery.data} {eventFilter} onfilterchange={(f) => eventFilter = f} onready={onPanelReady} onprepending={setTailFence} />
            {:else}
              <ChatView {events} {agents} sessions={detail.sessions} sessionId={activeSessionId} ephemeralText={ephemeralBuffers.get(activeSessionId ?? '')?.text ?? ''} ephemeralThinking={ephemeralThinkingBuffers.get(activeSessionId ?? '') ?? null} ephemeralToolCalls={ephemeralToolCalls.get(activeSessionId ?? '') ?? null} settledThinkingDuration={settledThinkingDurations.get(activeSessionId ?? '') ?? null} usageBySession={$usageBySession} {sessionIdentity} agentPool={poolQuery.data} {eventFilter} {viewedSessionSettled} onfilterchange={(f) => eventFilter = f} onthreadopen={(a, b) => { threadTarget = { sessionA: a, sessionB: b }; }} onready={onPanelReady} onprepending={setTailFence} />
            {/if}
          {/if}
          {#if panelPhase !== 'live'}
            <div class="events-loading" class:is-overlay={panelPhase === 'mounting'} aria-live="polite">
              <div class="events-loading-box">
                {#if panelPhase === 'fetch'}Loading session&#8230;{:else}Loading <span class="events-loading-count">{pendingCount}</span> events&#8230;{/if}
              </div>
            </div>
          {/if}
        </div>
      {/if}
    {/if}
  </div>

  {#if showDetailsOverlay && isMobile}
    <button class="details-backdrop" onclick={() => showDetailsOverlay = false} aria-label="Close execution details"></button>
    <div class="details-overlay">
      <div class="details-overlay-header">
        <button class="details-overlay-back" onclick={() => showDetailsOverlay = false} aria-label="Close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6" /></svg>
        </button>
        <span class="details-overlay-title">Execution Details</span>
      </div>
      <div class="details-overlay-body scroll-thin">
        <SidebarSessionTree
          sessions={detail.sessions}
          {agents}
          selectedSessionId={activeSessionId}
          {isTerminal}
          usageBySession={$usageBySession}
          poolAgents={poolQuery.data}
          {sessionIdentity}
          onselectsession={(id) => { selectedSessionId.set(id); showDetailsOverlay = false; }}
          onstatuschange={() => queryClient.invalidateQueries({ queryKey: ['execution', executionId] })}
        />
      </div>
    </div>
  {/if}

  <AlertDialog.Root bind:open={showTerminateDialog}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay class="modal-overlay" />
      <AlertDialog.Content class="modal-content">
        <AlertDialog.Title class="modal-title">{isCompletionEligible ? 'Complete' : 'Cancel'} Execution</AlertDialog.Title>
        <AlertDialog.Description class="modal-description">
          {isCompletionEligible
            ? 'Mark this execution as complete? All active sessions will be stopped. This cannot be undone.'
            : 'Cancel this execution? The agent will be stopped.'}
        </AlertDialog.Description>
        {#if terminateError}
          <div class="modal-error">{terminateError}</div>
        {/if}
        <div class="modal-actions">
          <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Keep Running</AlertDialog.Cancel>
          <button class="alert-btn {isCompletionEligible ? 'alert-btn-primary' : 'alert-btn-danger'}" disabled={terminateMut.isPending} onclick={handleTerminate}>
            {terminateMut.isPending ? 'Terminating...' : isCompletionEligible ? 'Complete Execution' : 'Cancel Execution'}
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
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 1rem;
    flex-shrink: 0;
    min-height: 36px;
    border-bottom: 1px solid hsl(var(--border));
  }

  .detail-title {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin: 0;
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

  .view-toggle-wrapper {
    position: relative;
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

  .events-panel {
    position: relative;
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .events-loading {
    flex: 1;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  /* Held over the mounting view so its first frame + scroll land unseen. */
  .events-loading.is-overlay {
    position: absolute;
    inset: 0;
    background: hsl(var(--background));
    z-index: 2;
  }

  .events-loading-box {
    padding: 0.375rem 0.75rem;
    border-radius: var(--radius);
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    animation: events-loading-pulse 2s ease-in-out infinite;
  }

  .events-loading-count {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  @keyframes events-loading-pulse {
    0%, 100% { box-shadow: 0 0 0 1px hsl(var(--border)); }
    50% { box-shadow: 0 0 8px 1px hsl(var(--muted-foreground) / 0.25); }
  }

  .detail-error {
    color: hsl(var(--status-danger));
  }

  .action-error {
    padding: 0.25rem 1rem;
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

  .overflow-menu-wrapper {
    position: relative;
    display: none;
  }

  .overflow-menu-btn {
    width: 2rem;
    height: 1.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.875rem;
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.1em;
  }

  .overflow-menu {
    position: absolute;
    top: 100%;
    right: 0;
    margin-top: 0.25rem;
    min-width: 10rem;
    padding: 0.25rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    box-shadow: 0 4px 12px hsl(var(--shadow-hsl) / 0.2);
    z-index: 60;
    display: flex;
    flex-direction: column;
    gap: 0.0625rem;
  }

  .overflow-item {
    padding: 0.375rem 0.625rem;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--foreground));
    font-size: 0.6875rem;
    font-weight: 500;
    cursor: pointer;
    text-align: left;
  }

  .overflow-item:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .overflow-item:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .details-backdrop {
    position: fixed;
    inset: 0;
    z-index: 49;
    background: rgba(0, 0, 0, 0.4);
    border: none;
    cursor: default;
  }

  .details-overlay {
    position: fixed;
    top: 0;
    bottom: 0;
    right: 0;
    left: 0;
    z-index: 50;
    display: flex;
    flex-direction: column;
    background: hsl(var(--background));
  }

  .details-overlay-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid hsl(var(--border));
    flex-shrink: 0;
  }

  .details-overlay-back {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1.75rem;
    height: 1.75rem;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
  }

  .details-overlay-back svg {
    width: 16px;
    height: 16px;
  }

  .details-overlay-back:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .details-overlay-title {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .details-overlay-body {
    flex: 1;
    overflow-y: auto;
  }

  @media (max-width: 768px) {
    .events-header .section-heading,
    .events-header .sse-indicator {
      display: none;
    }
    .events-header {
      padding: 0.25rem 1rem;
    }
    .desktop-only-actions {
      display: none;
    }
    .overflow-menu-wrapper {
      display: block;
    }
  }
</style>
