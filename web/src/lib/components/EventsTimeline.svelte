<script lang="ts">
  import { tick } from 'svelte';
  import type { Event } from '../types';
  import { isMessagePayload, isStateChangePayload, isAskUserData, isDelegateData, isHandoffResultData } from '../types';
  interface Props {
    events: Event[];
  }

  let { events }: Props = $props();
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
    id: number;
    time: string;
    icon: string;
    iconClass: string;
    text: string;
  }

  function parseEvent(ev: Event): ParsedEvent | null {
    const base = { id: ev.id, time: formatTime(ev.created_at) };

    if (isStateChangePayload(ev.payload)) {
      const p = ev.payload;
      return {
        ...base,
        icon: '\u25CF',
        iconClass: 'state-change',
        text: p.from ? `${p.from} \u2192 ${p.to}` : `started \u2192 ${p.to}`,
      };
    }

    if (isMessagePayload(ev.payload)) {
      const msg = ev.payload;

      for (const part of msg.parts) {
        if (part.kind === 'data') {
          const d = part.data;

          if (isAskUserData(d)) {
            // Hide non-primary batch events
            if (d.batch_index > 0) return null;

            if (d.importance === 'fyi') {
              return {
                ...base,
                icon: '\u2139',
                iconClass: 'fyi',
                text: `FYI: ${truncate(d.question, 80)}`,
              };
            }

            const qText = d.batch_size > 1
              ? `Asked ${d.batch_size} questions: "${truncate(d.question, 60)}" + ${d.batch_size - 1} more`
              : `Asked: "${truncate(d.question, 80)}"`;
            return {
              ...base,
              icon: '\u26A0',
              iconClass: 'question',
              text: qText,
            };
          }

          if (isDelegateData(d)) {
            return {
              ...base,
              icon: '\u2192',
              iconClass: 'delegate',
              text: `Delegated to ${d.agent}`,
            };
          }

          if (isHandoffResultData(d)) {
            return {
              ...base,
              icon: '\u2713',
              iconClass: 'handoff',
              text: `Child completed: "${truncate(d.message, 80)}"`,
            };
          }
        }

        if (part.kind === 'text') {
          if (msg.role === 'user') {
            return {
              ...base,
              icon: '\u25B6',
              iconClass: 'user',
              text: `User: ${truncate(part.text, 100)}`,
            };
          }
          return {
            ...base,
            icon: '\u25CF',
            iconClass: 'agent',
            text: truncate(part.text, 120),
          };
        }
      }
    }

    return null;
  }

  let parsed = $derived(events
    .map(parseEvent)
    .filter((e): e is ParsedEvent => e !== null));
</script>

<div class="timeline-section">
  <div class="timeline-scroll scroll-thin" bind:this={scrollContainer} onscroll={handleScroll}>
    {#if parsed.length === 0}
      <div class="timeline-empty">No events yet</div>
    {:else}
      {#each parsed as ev (ev.id)}
        <div class="timeline-entry">
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
    border-radius: 0.375rem;
    background: hsl(var(--muted) / 0.25);
  }

  .timeline-entry:hover {
    background: hsl(var(--muted) / 0.5);
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
    font-size: 0.75rem;
  }

  .ev-icon.state-change { color: hsl(var(--muted-foreground)); }
  .ev-icon.question { color: hsl(var(--status-attention)); }
  .ev-icon.fyi { color: hsl(var(--status-working)); }
  .ev-icon.delegate { color: hsl(var(--status-working)); }
  .ev-icon.handoff { color: hsl(var(--status-success)); }
  .ev-icon.user { color: hsl(var(--primary)); }
  .ev-icon.agent { color: hsl(var(--muted-foreground)); }

  .ev-text {
    flex: 1;
    word-break: break-word;
    color: hsl(var(--foreground));
  }
</style>
