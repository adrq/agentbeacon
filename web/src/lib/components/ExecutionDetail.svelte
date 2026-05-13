<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import type { Agent, AgentType, Event as BeaconEvent, EphemeralEvent, MessagePayload } from '../types';
  import { isMessagePayload, isCompactionData } from '../types';
  import { normalizeDataPart } from '../normalize';
  import { api } from '../api';
  import { executionDetailQuery, sessionEventsQuery, terminateExecutionMutation, executionAgentsQuery, recoverSessionMutation, executionSessionsQuery, buildSessionIdentityMap } from '../queries/executions';
  import { agentsQuery } from '../queries/agents';
  import { useQueryClient } from '@tanstack/svelte-query';
  import { connectExecutionSSE, type SSEConnection } from '../sse';
  import { untrack } from 'svelte';
  import StatusBadge from './StatusBadge.svelte';
  import QuestionBanner from './QuestionBanner.svelte';
  import EventsTimeline from './EventsTimeline.svelte';
  import ChatView from './ChatView.svelte';
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
  let settledThinkingDurations = $state<Map<string, { durationMs: number; startedAt: string }>>(new Map());
  let lastPersistedSeq = new Map<string, number>();
  // Running total of persisted text length per session, used to decide when
  // persisted content has caught up with the ephemeral buffer.
  let persistedTextLen = new Map<string, number>();

  // Event filter state (shared between Chat and Log views, resets on exec change)
  let eventFilter = $state<EventFilter>('all');

  // Org chart overview toggle
  let showOverview = $state(false);

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
  // (e.g. backfill on page load) into a single batch of invalidations.
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
  let sseReconnecting = $state(false);
  let sseConnection = $state<SSEConnection | null>(null);

  // Tracks event ids whose usage/compaction side effects have been applied.
  // De-dupes between the SSE callback (live events) and the polling fallback
  // $effect. Needed because SSE reconnect backfills already-polled events,
  // and naive re-processing would double-count compactions (non-idempotent).
  // Cleared on execution change (same lifetime as usageBySession).
  const processedUsageEventIds = new Set<number>();

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
      overflowMenuOpen = false;
      showDetailsOverlay = false;
      const hashView = getHashViewParam();
      if (hashView) viewMode = hashView;
      lastPersistedSeq.clear();
      persistedTextLen.clear();
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

  // Helper: get or lazily create a usage entry for a session
  function getOrCreateUsage(sessionId: string) {
    return $usageBySession.get(sessionId) ?? {
      usedTokens: 0, inputTokens: 0, outputTokens: 0, contextWindow: 0,
      compactions: 0, available: false, supportsContextPercentage: false,
    };
  }

  // Apply usage/compaction side effects for one event. Used by both the SSE
  // callback and the polling-fallback $effect so the context indicator works
  // in SSE-only, polling-only, and mixed-delivery scenarios. The processed-id
  // set de-dupes: compaction increment is non-idempotent, and SSE reconnect
  // backfills already-polled events.
  function applyUsageFromEvent(event: BeaconEvent) {
    if (event.event_type !== 'message' || !event.session_id) return;
    if (processedUsageEventIds.has(event.id)) return;
    processedUsageEventIds.add(event.id);
    const at = agentTypeForSession(event.session_id);
    const payload = event.payload as MessagePayload;
    for (const part of payload.parts ?? []) {
      if (!('data' in part)) continue;
      const d = (part as { data: unknown }).data;
      if (typeof d !== 'object' || d === null) continue;
      const dataObj = d as { type?: string; method?: string; tokenUsage?: unknown; [key: string]: unknown };
      if (!dataObj.type && !dataObj.tokenUsage && !dataObj.method) continue;
      const norm = normalizeDataPart(at, dataObj as Record<string, unknown>);
      if (norm.normalized === 'usage') {
        const current = getOrCreateUsage(event.session_id);
        const next = new Map($usageBySession);
        next.set(event.session_id, {
          ...current,
          // Use || not ?? — Claude's usage_snapshot sends input_tokens: 0 meaning
          // "not populated", not "zero tokens". Treating 0 as falsy is intentional.
          usedTokens: norm.usedTokens || current.usedTokens,
          inputTokens: norm.inputTokens || current.inputTokens,
          outputTokens: norm.outputTokens || current.outputTokens,
          contextWindow: norm.modelContextWindow ?? current.contextWindow,
        });
        usageBySession.set(next);
      } else if (isCompactionData(dataObj as { type: string; [key: string]: unknown })) {
        const current = getOrCreateUsage(event.session_id);
        const next = new Map($usageBySession);
        next.set(event.session_id, {
          ...current,
          compactions: current.compactions + 1,
        });
        usageBySession.set(next);
      }
    }
  }

  // Track when the initial REST events query has loaded so SSE can skip replay
  let initialEventsLoaded = $state(false);
  $effect(() => {
    if (eventsQuery.isSuccess && !initialEventsLoaded) {
      initialEventsLoaded = true;
    }
  });

  // SSE connection lifecycle — stay live until tree is fully settled
  $effect(() => {
    const execId = executionId;
    const settled = isSettled;
    const stillLoading = detailQuery.isLoading;
    if (settled || stillLoading || poolQuery.isLoading || !initialEventsLoaded) {
      sseActive = false;
      return;
    }

    // Snapshot the max event ID from the active session's REST cache.
    // Use untrack to avoid reactive dep on cache data (changes on every SSE event).
    const maxEventId = untrack(() => {
      const cached = queryClient.getQueryData<BeaconEvent[]>(['session-events', activeSessionId]);
      if (cached?.length) return cached[cached.length - 1].id;
      return 0;
    });

    const conn = connectExecutionSSE(
      execId,
      (event: BeaconEvent) => {
        // Capture whether this is a new event or an SSE replay/dedupe hit.
        // Replays (backoff reconnect, manual reconnect — no Last-Event-ID
        // preservation) can redeliver already-processed events. All
        // downstream side effects (usage accumulators, ephemeral text
        // length counters) must run only for new events, or they
        // double-count.
        let isNewEvent = true;
        queryClient.setQueryData(
          ['session-events', event.session_id],
          (old: BeaconEvent[] | undefined) => {
            if (!old) return [event];
            if (old.some(e => e.id === event.id)) {
              isNewEvent = false;
              return old;
            }
            return [...old, event];
          },
        );

        // Usage accumulation carries its own per-event dedupe set, so it
        // must run for both fresh and replayed events — specifically, an
        // SSE reconnect that backfills events already delivered via
        // polling would otherwise never reach it under the isNewEvent gate.
        applyUsageFromEvent(event);

        if (!isNewEvent) return;

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
          if (persistedText) {
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
              lastPersistedSeq.set(event.session_id, Number.MAX_SAFE_INTEGER);
            }
          }
        }
      },
      (eph: EphemeralEvent) => {
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
        sseActive = false;
        sseReconnecting = false;
      },
      () => { sseReconnecting = true; },
      maxEventId || undefined,
    );
    sseConnection = conn;

    return () => {
      conn.close();
      sseActive = false;
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
  const eventsQuery = sessionEventsQuery(
    () => activeSessionId,
    () => isSettled,
    () => sseActive,
  );
  let events = $derived(eventsQuery.data ?? []);

  // Drive usage/compaction accumulation from the polling cache so the
  // context indicator works even when SSE is unavailable (permanent
  // fallback after MAX_CONSECUTIVE_ERRORS, terminal executions where SSE
  // never attaches, or mid-lifecycle executions where polling delivers
  // events before the SSE backfill races in). The per-event dedupe set
  // guarantees each event contributes exactly once regardless of path.
  // Wait for execution + pool data to resolve so agent-type lookup doesn't
  // fall back to claude_sdk for Codex sessions, permanently mis-normalizing
  // the initial event batch.
  $effect(() => {
    if (detailQuery.isLoading || poolQuery.isLoading) return;
    for (const event of events) {
      applyUsageFromEvent(event);
    }
  });

  // Usage/compaction extraction from REST-loaded events lives in
  // applyUsageFromEvent, which is called from both the SSE callback and the
  // polling fallback $effect (above). The per-event dedupe set guarantees
  // each event contributes exactly once regardless of delivery path.

  let inputSessionId = $derived(
    detail?.sessions.find(s => !s.parent_session_id)?.id ?? activeSessionId
  );
  const inputEventsQuery = sessionEventsQuery(
    () => inputSessionId !== activeSessionId ? inputSessionId : null,
    () => isSettled,
    () => sseActive,
  );
  let inputEvents = $derived(
    inputSessionId === activeSessionId ? events : (inputEventsQuery.data ?? [])
  );

  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
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
        const sessionEvents = await api.getSessionEvents(rootSession.id);
        const firstMsg = sessionEvents.find(e =>
          e.event_type === 'message' && isMessagePayload(e.payload) && e.payload.role === 'ROLE_USER'
        );
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

    <QuestionBanner execution={detail.execution} sessions={detail.sessions} events={inputEvents} {agents} />

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
      </div>

      {#if viewMode === 'log'}
        <EventsTimeline {events} {agents} sessions={detail.sessions} {eventFilter} onfilterchange={(f) => eventFilter = f} />
      {:else if viewMode === 'chat'}
        <ChatView {events} {agents} sessions={detail.sessions} sessionId={activeSessionId} ephemeralText={ephemeralBuffers.get(activeSessionId ?? '')?.text ?? ''} ephemeralThinking={ephemeralThinkingBuffers.get(activeSessionId ?? '') ?? null} settledThinkingDuration={settledThinkingDurations.get(activeSessionId ?? '') ?? null} usageBySession={$usageBySession} {sessionIdentity} {eventFilter} onfilterchange={(f) => eventFilter = f} />
      {:else if viewMode === 'diff'}
        <DiffPanel sessionId={activeSessionId} {isTerminal} />
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
