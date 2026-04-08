<script lang="ts">
  import { Popover } from 'bits-ui';
  import type { SessionSummary, SessionIdentity } from '../types';

  interface Props {
    nodes: SessionSummary[];
    sessionIdentity?: Map<string, SessionIdentity>;
    now: number;
    onselectsession?: (sessionId: string) => void;
  }

  let { nodes, sessionIdentity, now, onselectsession }: Props = $props();

  let summaryText = $derived((() => {
    const counts: Record<string, number> = {};
    for (const n of nodes) {
      counts[n.status] = (counts[n.status] ?? 0) + 1;
    }
    const parts: string[] = [];
    if (counts['completed']) parts.push(`${counts['completed']} completed`);
    if (counts['canceled']) parts.push(`${counts['canceled']} canceled`);
    return parts.join(', ');
  })());

  function formatDuration(startIso: string, endIso?: string | null): string {
    const start = new Date(startIso).getTime();
    const end = endIso ? new Date(endIso).getTime() : now;
    const diff = Math.floor((end - start) / 1000);
    if (diff < 60) return `${diff}s`;
    const m = Math.floor(diff / 60);
    const s = diff % 60;
    if (m < 60) return `${m}m${s > 0 ? ` ${s}s` : ''}`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  function dotColor(status: string): string {
    switch (status) {
      case 'completed': return 'hsl(var(--status-success))';
      case 'canceled': return 'hsl(var(--muted-foreground))';
      default: return 'hsl(var(--muted-foreground))';
    }
  }

  function getSlug(session: SessionSummary): string {
    return sessionIdentity?.get(session.id)?.slug ?? session.id.slice(0, 8);
  }

  let popoverOpen = $state(false);
</script>

<Popover.Root bind:open={popoverOpen}>
  <Popover.Trigger>
    {#snippet child({ props })}
      <button
        {...props}
        class="org-chart-group-node"
        aria-label="{nodes.length} grouped sessions: {summaryText}"
      >
        <div class="group-dots">
          {#each nodes as node (node.id)}
            <span class="group-dot" style="background: {dotColor(node.status)}"></span>
          {/each}
        </div>
        <span class="group-count">{summaryText}</span>
      </button>
    {/snippet}
  </Popover.Trigger>

  <Popover.Content class="org-chart-popover" sideOffset={4}>
    <div class="popover-list">
      {#each nodes as node (node.id)}
        <button
          class="popover-item"
          onclick={() => { popoverOpen = false; onselectsession?.(node.id); }}
        >
          <span class="popover-dot" style="background: {dotColor(node.status)}"></span>
          <span class="popover-slug">{getSlug(node)}</span>
          <span class="popover-duration">{formatDuration(node.created_at, node.updated_at)}</span>
        </button>
      {/each}
    </div>
  </Popover.Content>
</Popover.Root>

<style>
  .org-chart-group-node {
    position: absolute;
    width: 160px;
    height: 50px;
    box-sizing: border-box;
    padding: 6px 10px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    cursor: pointer;
    transition: background 0.15s;
    font-family: inherit;
    text-align: left;
    color: hsl(var(--foreground));
    outline: none;
  }

  .org-chart-group-node:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .org-chart-group-node:focus-visible {
    outline: 2px solid hsl(var(--primary));
    outline-offset: 2px;
  }

  .group-dots {
    display: flex;
    gap: 3px;
    flex-wrap: wrap;
  }

  .group-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .group-count {
    font-size: 0.8125rem;
    font-weight: 400;
    color: hsl(var(--muted-foreground));
  }

  .popover-list {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 200px;
    overflow-y: auto;
  }

  .popover-item {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border: none;
    background: transparent;
    cursor: pointer;
    border-radius: var(--radius-sm);
    transition: background 0.1s;
    font-family: inherit;
    text-align: left;
    color: hsl(var(--foreground));
    width: 100%;
  }

  .popover-item:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .popover-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .popover-slug {
    font-size: 0.6875rem;
    font-weight: 500;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .popover-duration {
    font-size: 0.625rem;
    font-weight: 500;
    font-family: var(--font-mono, monospace);
    font-variant-numeric: tabular-nums;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  :global(.org-chart-popover) {
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    padding: 6px;
    min-width: 160px;
    max-width: 240px;
    z-index: 50;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  }
</style>
