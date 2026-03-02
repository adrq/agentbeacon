<script lang="ts">
  import { tick } from 'svelte';
  import type { Event, Agent, SessionSummary, AgentType } from '../types';
  import { isMessagePayload, isStateChangePayload, isEscalateData, isDelegateData, isTurnCompleteData, isPlanData } from '../types';
  import { normalizeDataPart } from '../normalize';
  interface Props {
    events: Event[];
    agents?: Agent[];
    sessions?: SessionSummary[];
  }

  let { events, agents = [], sessions = [] }: Props = $props();

  function resolveAgentType(sessionId: string | null): AgentType {
    const session = sessions.find(s => s.id === sessionId);
    const agent = agents.find(a => a.id === session?.agent_id);
    return agent?.agent_type ?? 'acp';
  }
  let scrollContainer: HTMLDivElement | undefined = $state(undefined);
  let shouldAutoScroll = $state(true);

  function handleScroll() {
    if (!scrollContainer) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollContainer;
    shouldAutoScroll = scrollHeight - scrollTop - clientHeight < 40;
  }

  $effect(() => {
    const _len = parsed.length; // dependency: re-run when entries change
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
  }

  function parseEventParts(ev: Event, seenToolCalls: Set<string>): ParsedEvent[] {
    const time = formatTime(ev.created_at);

    if (isStateChangePayload(ev.payload)) {
      const p = ev.payload;
      const isFailed = p.to === 'failed';
      return [{
        key: `${ev.id}`,
        time,
        icon: isFailed ? '\u2716' : '\u25CF',
        iconClass: isFailed ? 'error' : 'state-change',
        text: p.from ? `${p.from} \u2192 ${p.to}` : `started \u2192 ${p.to}`,
      }];
    }

    if (isMessagePayload(ev.payload)) {
      const msg = ev.payload;
      const entries: ParsedEvent[] = [];
      const agentType = resolveAgentType(ev.session_id);

      for (let i = 0; i < msg.parts.length; i++) {
        const part = msg.parts[i];
        const key = `${ev.id}-${i}`;

        if (part.kind === 'data') {
          const d = part.data as Record<string, unknown>;

          // Platform events
          if (isEscalateData(d as unknown as import('../types').DataPartPayload)) {
            const ask = d as unknown as import('../types').EscalateData;
            if (ask.batch_index > 0) continue;
            if (ask.importance === 'fyi') {
              entries.push({ key, time, icon: '\u2139', iconClass: 'fyi', text: `FYI: ${truncate(ask.question, 80)}` });
            } else {
              const qText = ask.batch_size > 1
                ? `Asked ${ask.batch_size} questions: "${truncate(ask.question, 60)}" + ${ask.batch_size - 1} more`
                : `Asked: "${truncate(ask.question, 80)}"`;
              entries.push({ key, time, icon: '\u26A0', iconClass: 'question', text: qText });
            }
            continue;
          }
          if (isDelegateData(d as unknown as import('../types').DataPartPayload)) {
            const del = d as unknown as import('../types').DelegateData;
            entries.push({ key, time, icon: '\u2192', iconClass: 'delegate', text: `Delegated to ${del.agent}` });
            continue;
          }
          if (isTurnCompleteData(d as unknown as import('../types').DataPartPayload)) {
            const tc = d as unknown as import('../types').TurnCompleteData;
            entries.push({ key, time, icon: '\u21A9', iconClass: 'turn-complete', text: `Child reported: "${truncate(tc.message, 80)}"` });
            continue;
          }

          // Normalize SDK/ACP data parts
          const norm = normalizeDataPart(agentType, d);
          switch (norm.normalized) {
            case 'tool_call':
              if (norm.toolCallId) seenToolCalls.add(norm.toolCallId);
              entries.push({ key, time, icon: '\u2699', iconClass: 'agent', text: norm.title || 'Unknown tool' });
              break;
            case 'tool_result':
              if (norm.toolCallId && seenToolCalls.has(norm.toolCallId) && !norm.isError) break;
              entries.push({ key, time, icon: '\u2699', iconClass: 'agent', text: `Result (${norm.toolCallId})` });
              break;
            case 'thinking':
              entries.push({ key, time, icon: '\u22EF', iconClass: 'agent', text: truncate(norm.text, 200) });
              break;
            case 'unknown': {
              const rawType = norm.raw.type as string | undefined;
              if (rawType === 'plan' && isPlanData(norm.raw as unknown as import('../types').DataPartPayload)) {
                const plan = norm.raw as unknown as import('../types').PlanData;
                entries.push({ key, time, icon: '\u2630', iconClass: 'agent', text: `Plan (${plan.entries.length} steps)` });
              } else if (rawType === 'current_mode_update') {
                entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[mode_change]` });
              } else if (rawType === 'available_commands_update') {
                entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[available_commands]` });
              } else {
                entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[${rawType ?? 'data'}]` });
              }
              break;
            }
          }
        } else if (part.kind === 'file') {
          const name = 'file' in part && typeof part.file === 'object' && part.file && 'name' in part.file
            ? (part.file as { name: string }).name : 'file';
          entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: `[file] ${name}` });
        } else if (part.kind === 'text') {
          const text = part.text as string;
          if (msg.role === 'user') {
            entries.push({ key, time, icon: '\u25B6', iconClass: 'user', text: `User: ${truncate(text, 100)}` });
          } else {
            entries.push({ key, time, icon: '\u25CF', iconClass: 'agent', text: truncate(text, 120) });
          }
        } else {
          // Fallback for unknown part kinds (tool-use, thinking, cost, etc.)
          const label = part.kind;
          const detail = 'text' in part && typeof part.text === 'string'
            ? truncate(part.text, 80)
            : 'name' in part && typeof part.name === 'string'
              ? part.name
              : '';
          entries.push({ key, time, icon: '\u25A1', iconClass: 'agent', text: detail ? `[${label}] ${detail}` : `[${label}]` });
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
</script>

<div class="timeline-section">
  <div class="timeline-scroll scroll-thin" bind:this={scrollContainer} onscroll={handleScroll}>
    {#if parsed.length === 0}
      <div class="timeline-empty">No events yet</div>
    {:else}
      {#each parsed as ev (ev.key)}
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

  .ev-text {
    flex: 1;
    word-break: break-word;
    color: hsl(var(--foreground));
  }

  .error-entry .ev-text {
    color: hsl(var(--status-danger));
  }
</style>
