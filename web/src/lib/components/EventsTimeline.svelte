<script lang="ts">
  import { tick } from 'svelte';
  import type { Event, Agent, AgentPoolEntry, SessionSummary, AgentType } from '../types';
  import { isMessagePayload, isStateChangePayload, isEscalateData, isDelegateData, isTurnCompleteData, isPlanData, isCompactionData } from '../types';
  import { normalizeDataPart } from '../normalize';
  import { EVENT_FILTER_PILLS, matchesFilter, type EventFilter } from '../eventFilterGroups';
  import { api } from '../api';

  interface Props {
    events: Event[];
    agents?: Agent[];
    sessions?: SessionSummary[];
    agentPool?: AgentPoolEntry[];
    eventFilter?: EventFilter;
    onfilterchange?: (filter: EventFilter) => void;
  }

  let { events, agents = [], sessions = [], agentPool, eventFilter = 'all', onfilterchange }: Props = $props();

  function resolveAgentType(sessionId: string | null): AgentType {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return 'claude_sdk';
    const poolAgent = agentPool?.find(a => a.agent_id === session.agent_id);
    if (poolAgent) return (poolAgent.agent_type as AgentType) ?? 'claude_sdk';
    const globalAgent = agents.find(a => a.id === session.agent_id);
    return (globalAgent?.agent_type as AgentType) ?? 'claude_sdk';
  }
  let scrollContainer: HTMLDivElement | undefined = $state(undefined);
  let shouldAutoScroll = $state(true);

  function handleScroll() {
    if (!scrollContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    shouldAutoScroll = scrollHeight - scrollTop - clientHeight < 40;
  }

  $effect(() => {
    const _len = events.length; // dependency: re-run when any event arrives
    if (shouldAutoScroll && scrollContainer) {
      tick().then(() => {
        if (scrollContainer) scrollContainer.scrollTop = scrollContainer.scrollHeight;
      });
    }
  });

  function formatTime(iso: string): string {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function truncate(text: string, max: number): string {
    return text.length > max ? text.slice(0, max) + '\u2026' : text;
  }

  interface ParsedEvent {
    key: string;
    time: string;
    icon: string;
    iconClass: string;
    text: string;
    entryType: string;
  }

  function parseEventParts(ev: Event, seenToolCalls: Set<string>): ParsedEvent[] {
    const time = formatTime(ev.created_at);

    if (isStateChangePayload(ev.payload)) {
      const p = ev.payload;
      const isFailed = p.outcome === 'failed' || p.to === 'failed';
      // Build state transition text
      let stateText: string;
      if (p.executor_state) {
        stateText = `executor: ${p.executor_state}`;
      } else if (p.desired && p.outcome) {
        stateText = `${p.desired} \u2192 ${p.outcome}`;
      } else if (p.desired) {
        stateText = `desired: ${p.desired}`;
      } else if (p.from !== undefined) {
        stateText = p.from ? `${p.from} \u2192 ${p.to}` : `started \u2192 ${p.to}`;
      } else {
        stateText = JSON.stringify(p);
      }
      return [{
        key: `${ev.id}`,
        time,
        icon: isFailed ? '\u2716' : '\u25CF',
        iconClass: isFailed ? 'error' : 'state-change',
        text: stateText,
        entryType: isFailed ? 'error' : 'state',
      }];
    }

    // Platform events with structured parts (turn_complete, delegate, escalate, etc.)
    if (ev.event_type === 'platform' && ev.payload && 'parts' in ev.payload && !('role' in ev.payload)) {
      const entries: ParsedEvent[] = [];
      const parts = (ev.payload as { parts: Array<Record<string, unknown>> }).parts ?? [];
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        const key = `${ev.id}-${i}`;
        if ('data' in part) {
          const d = part.data as Record<string, unknown>;
          if (isTurnCompleteData(d as unknown as import('../types').DataPartPayload)) {
            const tc = d as unknown as import('../types').TurnCompleteData;
            if (tc.child_session_id && tc.child_session_id === ev.session_id) {
              entries.push({ key, time, icon: '\u25CB', iconClass: 'state-change', text: 'Turn complete', entryType: 'state' });
              continue;
            }
            const childSession = sessions.find(s => s.id === tc.child_session_id);
            const childAgentName = agents.find(a => a.id === childSession?.agent_id)?.name ?? 'Child';
            const msg = `${childAgentName} turn complete`;
            entries.push({ key, time, icon: '\u21A9', iconClass: 'turn-complete', text: `Child reported: "${truncate(msg, 80)}"`, entryType: 'child_response' });
          } else if (isDelegateData(d as unknown as import('../types').DataPartPayload)) {
            const del = d as unknown as import('../types').DelegateData;
            entries.push({ key, time, icon: '\u2192', iconClass: 'delegate', text: `Delegated to ${del.agent}`, entryType: 'tool' });
          } else if (isEscalateData(d as unknown as import('../types').DataPartPayload)) {
            const ask = d as unknown as import('../types').EscalateData;
            if (ask.batch_index === 0) {
              if (ask.importance === 'fyi') {
                entries.push({ key, time, icon: '\u2139', iconClass: 'fyi', text: `FYI: ${truncate(ask.question, 80)}`, entryType: 'fyi' });
              } else {
                entries.push({ key, time, icon: '\u26A0', iconClass: 'question', text: `Asked: "${truncate(ask.question, 80)}"`, entryType: 'tool' });
              }
            }
          }
        }
      }
      if (entries.length > 0) return entries;
      return [{ key: `${ev.id}`, time, icon: '\u25CB', iconClass: 'state-change', text: 'platform event', entryType: 'state' }];
    }

    // Bare-object platform events (crash/message-loss warnings)
    if (ev.event_type === 'platform' && ev.payload && !('parts' in ev.payload) && !('role' in ev.payload)) {
      const p = ev.payload as Record<string, unknown>;
      if (p.type === 'message_delivered' || p.type === 'child_continued') return [];
      const msg = typeof p.message === 'string' ? p.message : undefined;
      const error = typeof p.error === 'string' ? p.error : undefined;
      if (!msg && !error) return [];
      return [{ key: `${ev.id}`, time, icon: '\u26A0', iconClass: 'warning', text: msg || error!, entryType: 'state' }];
    }

    if (isMessagePayload(ev.payload)) {
      const msg = ev.payload;
      const entries: ParsedEvent[] = [];
      const agentType = resolveAgentType(ev.session_id);

      // Pre-scan for sender metadata (inter-agent message)
      const senderPart = msg.parts.find(
        p => 'data' in p && (p.data as Record<string, unknown>)?.type === 'sender'
      );
      const senderName = senderPart
        ? ((senderPart as { data: Record<string, unknown> }).data.name as string) || 'unknown'
        : null;

      for (let i = 0; i < msg.parts.length; i++) {
        const part = msg.parts[i];
        const key = `${ev.id}-${i}`;

        if ('data' in part) {
          const d = part.data as Record<string, unknown>;

          // Skip sender metadata part — handled via pre-scan above
          if (d.type === 'sender') continue;

          // Normalize SDK/ACP data parts early so routing uses normalized types
          const norm = normalizeDataPart(agentType, d);

          // Skip usage metadata — don't show in log view
          if (norm.normalized === 'usage') continue;

          // Platform events
          if (isEscalateData(d as unknown as import('../types').DataPartPayload)) {
            const ask = d as unknown as import('../types').EscalateData;
            if (ask.batch_index > 0) continue;
            if (ask.importance === 'fyi') {
              entries.push({ key, time, icon: '\u2139', iconClass: 'fyi', text: `FYI: ${truncate(ask.question, 80)}`, entryType: 'fyi' });
            } else {
              const qText = ask.batch_size > 1
                ? `Asked ${ask.batch_size} questions: "${truncate(ask.question, 60)}" + ${ask.batch_size - 1} more`
                : `Asked: "${truncate(ask.question, 80)}"`;
              entries.push({ key, time, icon: '\u26A0', iconClass: 'question', text: qText, entryType: 'tool' });
            }
            continue;
          }
          if (isDelegateData(d as unknown as import('../types').DataPartPayload)) {
            const del = d as unknown as import('../types').DelegateData;
            entries.push({ key, time, icon: '\u2192', iconClass: 'delegate', text: `Delegated to ${del.agent}`, entryType: 'tool' });
            continue;
          }
          if (isTurnCompleteData(d as unknown as import('../types').DataPartPayload)) {
            const tc = d as unknown as import('../types').TurnCompleteData;
            if (tc.child_session_id && tc.child_session_id === ev.session_id) {
              entries.push({ key, time, icon: '\u25CB', iconClass: 'state-change', text: 'Turn complete', entryType: 'state' });
              continue;
            }
            const childSession = sessions.find(s => s.id === tc.child_session_id);
            const childAgentName = agents.find(a => a.id === childSession?.agent_id)?.name ?? 'Child';
            const msg = `${childAgentName} turn complete`;
            entries.push({ key, time, icon: '\u21A9', iconClass: 'turn-complete', text: `Child reported: "${truncate(msg, 80)}"`, entryType: 'child_response' });
            continue;
          }

          if (isCompactionData(d as unknown as import('../types').DataPartPayload)) {
            entries.push({
              key, time,
              icon: '\u21BB',  // ↻
              iconClass: 'state-change',
              text: 'Context compacted',
              entryType: 'state',
            });
            continue;
          }
          switch (norm.normalized) {
            case 'tool_call':
              if (norm.toolCallId) seenToolCalls.add(norm.toolCallId);
              // TodoWrite → compact "Tasks (N items)" entry
              if (norm.title === 'TodoWrite' && norm.input && typeof norm.input === 'object') {
                const input = norm.input as { todos?: unknown[] };
                if (Array.isArray(input.todos)) {
                  entries.push({ key, time, icon: '\u2630', iconClass: 'agent', text: `Tasks (${input.todos.length} items)`, entryType: 'todo_write' });
                  break;
                }
              }
              entries.push({ key, time, icon: '\u2699', iconClass: 'agent', text: norm.title || 'Unknown tool', entryType: 'tool_group' });
              break;
            case 'tool_result':
              if (norm.toolCallId && seenToolCalls.has(norm.toolCallId) && !norm.isError) break;
              entries.push({ key, time, icon: '\u2699', iconClass: 'agent', text: `Result (${norm.toolCallId})`, entryType: 'tool_group' });
              break;
            case 'thinking':
              if (norm.text) entries.push({ key, time, icon: '\u22EF', iconClass: 'agent', text: truncate(norm.text, 200), entryType: 'thinking' });
              break;
            case 'text':
              entries.push({ key, time, icon: '\u25CF', iconClass: 'agent', text: truncate(norm.text, 120), entryType: 'agent' });
              break;
            case 'error':
              entries.push({ key, time, icon: '\u2716', iconClass: 'error', text: truncate(norm.message, 160), entryType: 'error' });
              break;
            case 'fyi':
              entries.push({ key, time, icon: '\u2139', iconClass: 'fyi', text: truncate(norm.title, 160), entryType: 'fyi' });
              break;
            case 'debug': {
              const method = (norm.raw.method as string | undefined) ?? (norm.raw.type as string | undefined) ?? norm.reason;
              entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[${method}]`, entryType: 'debug_event' });
              break;
            }
            case 'unknown': {
              const rawType = norm.raw.type as string | undefined;
              if (rawType === 'plan' && isPlanData(norm.raw as unknown as import('../types').DataPartPayload)) {
                const plan = norm.raw as unknown as import('../types').PlanData;
                entries.push({ key, time, icon: '\u2630', iconClass: 'agent', text: `Plan (${plan.entries.length} steps)`, entryType: 'tool' });
              } else if (rawType === 'current_mode_update') {
                entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[mode_change]`, entryType: 'tool' });
              } else if (rawType === 'available_commands_update') {
                entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[available_commands]`, entryType: 'tool' });
              } else {
                entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[${rawType ?? 'data'}]`, entryType: 'data_fallback' });
              }
              break;
            }
          }
        } else if ('url' in part || 'raw' in part) {
          const name = part.filename ?? 'file';
          entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[file] ${name}`, entryType: 'tool' });
        } else if ('text' in part) {
          const text = (part as { text: string }).text;
          if (senderName) {
            entries.push({ key, time, icon: '\u2709', iconClass: 'lateral', text: `${senderName}: "${truncate(text, 80)}"`, entryType: 'lateral' });
          } else if (msg.role === 'ROLE_USER') {
            entries.push({ key, time, icon: '\u25B6', iconClass: 'user', text: `User: ${truncate(text, 100)}`, entryType: 'user' });
          } else {
            entries.push({ key, time, icon: '\u25CF', iconClass: 'agent', text: truncate(text, 120), entryType: 'agent' });
          }
        } else {
          // Fallback for unknown part shapes
          const label = 'unknown';
          const p = part as Record<string, unknown>;
          const detail = 'text' in p && typeof p.text === 'string'
            ? truncate(p.text, 80)
            : (p.filename as string) ?? '';
          entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: detail ? `[${label}] ${detail}` : `[${label}]`, entryType: 'tool' });
        }
      }

      return entries;
    }

    return [];
  }

  function parseAllEvents(evs: Event[]): ParsedEvent[] {
    const entries: ParsedEvent[] = [];
    const seenToolCalls = new Set<string>();

    for (const ev of evs) {
      const parts = parseEventParts(ev, seenToolCalls);
      entries.push(...parts);
    }
    return entries;
  }

  let parsed = $derived(parseAllEvents(events));

  let filteredParsed = $derived(
    parsed.filter(entry => matchesFilter(entry.entryType, eventFilter))
  );
</script>

<div class="timeline-section">
  <div class="event-filter-pills" role="radiogroup" aria-label="Filter events">
    {#each EVENT_FILTER_PILLS as pill}
      <button
        class="event-filter-pill"
        class:active={eventFilter === pill.value}
        role="radio"
        aria-checked={eventFilter === pill.value}
        onclick={() => onfilterchange?.(pill.value)}
      >{pill.label}</button>
    {/each}
  </div>
  <div class="timeline-scroll scroll-thin" bind:this={scrollContainer} onscroll={handleScroll}>
    {#if filteredParsed.length === 0}
      <div class="timeline-empty">{parsed.length === 0 ? 'No events yet' : 'No matching events'}</div>
    {:else}
      {#each filteredParsed as ev (ev.key)}
        <div class="timeline-entry" class:error-entry={ev.iconClass === 'error'}>
          <span class="ev-time">{ev.time}</span>
          <span class="ev-icon {ev.iconClass}">{ev.icon}</span>
          <span class="ev-text">{ev.text}</span>
        </div>
      {/each}
    {/if}
  </div>
</div>

<style>
  .event-filter-pills {
    display: flex;
    gap: 2px;
    padding: 0.375rem 1rem;
    flex-shrink: 0;
  }
  .event-filter-pill {
    padding: 0.1875rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.6875rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.1s, color 0.1s, border-color 0.1s;
  }
  .event-filter-pill:hover:not(.active) {
    background: hsl(var(--muted) / 0.5);
  }
  .event-filter-pill.active {
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
    border-color: hsl(var(--primary) / 0.3);
    font-weight: 600;
  }

  .timeline-section {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .timeline-scroll {
    flex: 1;
    overflow-y: auto;
    padding: 0 1rem 0.5rem;
  }

  .timeline-empty {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    padding: 1rem 0;
  }

  .timeline-entry {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.375rem 0.625rem;
    margin-bottom: 0.125rem;
    font-size: 0.8125rem;
    line-height: 1.4;
    border-radius: var(--radius);
    background: hsl(var(--muted) / 0.25);
  }

  .timeline-entry:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .timeline-entry.error-entry {
    background: hsl(var(--status-danger) / 0.08);
  }

  .timeline-entry.error-entry:hover {
    background: hsl(var(--status-danger) / 0.14);
  }

  .ev-time {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
    padding-top: 0.0625rem;
  }

  .ev-icon {
    width: 1rem;
    text-align: center;
    flex-shrink: 0;
    font-size: 0.6875rem;
  }

  .ev-icon.state-change { color: hsl(var(--muted-foreground)); }
  .ev-icon.error { color: hsl(var(--status-danger)); }
  .ev-icon.question { color: hsl(var(--status-attention)); }
  .ev-icon.fyi { color: hsl(var(--status-working)); }
  .ev-icon.delegate { color: hsl(var(--status-working)); }
  .ev-icon.turn-complete { color: hsl(var(--status-success)); }
  .ev-icon.user { color: hsl(var(--primary)); }
  .ev-icon.agent { color: hsl(var(--muted-foreground)); }
  .ev-icon.lateral { color: hsl(var(--status-working)); }

  .ev-text {
    flex: 1;
    word-break: break-word;
    color: hsl(var(--foreground));
  }

  .error-entry .ev-text {
    color: hsl(var(--status-danger));
  }
</style>
