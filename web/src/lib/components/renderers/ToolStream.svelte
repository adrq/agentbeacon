<script lang="ts">
  import type { NormalizedToolCall, NormalizedToolResult } from '../../normalize';
  import ToolGroup from './ToolGroup.svelte';

  interface ToolGroupEntry {
    call: NormalizedToolCall;
    result?: NormalizedToolResult;
    time: string;
  }

  interface Props {
    groups: ToolGroupEntry[];
    live: boolean;
  }

  let { groups, live }: Props = $props();

  let expanded = $state(false);
  let expandedDetailId: string | null = $state(null);
  let showAllLive = $state(false);

  let total = $derived(groups.length);
  let hasErrors = $derived(groups.some(g => g.result?.isError || g.call.status === 'failed'));
  let allSettled = $derived(groups.every(g =>
    g.call.status === 'completed' || g.call.status === 'failed' || g.result != null
  ));

  let typeBreakdown = $derived.by(() => {
    const counts = new Map<string, number>();
    for (const g of groups) {
      const name = (g.call.title || 'Tool').match(/^\w+/)?.[0] ?? 'Tool';
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `${count} ${name}`)
      .join(', ');
  });

  let summaryText = $derived.by(() => {
    const completedCount = groups.filter(g =>
      g.call.status === 'completed' || g.call.status === 'failed' || g.result != null
    ).length;
    if (allSettled) {
      return `${total} tool calls ${hasErrors ? 'completed with errors' : 'completed'} (${typeBreakdown})`;
    }
    return `${completedCount}/${total} tool calls (${typeBreakdown})`;
  });

  let visibleLiveStart = $derived(Math.max(0, groups.length - 3));
  let earlierCount = $derived(visibleLiveStart);

  function toggleExpanded() {
    expanded = !expanded;
    if (!expanded) expandedDetailId = null;
  }

  function toggleDetail(toolCallId: string) {
    expandedDetailId = expandedDetailId === toolCallId ? null : toolCallId;
  }

  function toggleShowAllLive() {
    showAllLive = !showAllLive;
  }

  function statusIndicator(g: ToolGroupEntry): string {
    if (g.result?.isError || g.call.status === 'failed') return '\u2717';
    if (g.call.status === 'completed' || g.result != null) return '\u2713';
    return '\u25CF';
  }

  function statusLabel(g: ToolGroupEntry): string {
    if (g.result?.isError || g.call.status === 'failed') return 'failed';
    if (g.call.status === 'completed' || g.result != null) return 'completed';
    if (g.call.status === 'running') return 'running';
    return 'pending';
  }
</script>

{#snippet logLine(g: ToolGroupEntry, idx: number)}
  {@const callId = g.call.toolCallId || `idx-${idx}`}
  {@const isDetailOpen = expandedDetailId === callId}
  <div class="ts-log-line" role="listitem">
    <button
      class="ts-log-line-header"
      onclick={() => toggleDetail(callId)}
      aria-expanded={isDetailOpen}
    >
      <span class="ts-status-indicator" class:ts-success={statusLabel(g) === 'completed'} class:ts-error={statusLabel(g) === 'failed'} class:ts-running={statusLabel(g) === 'running'}>{statusIndicator(g)}</span>
      <span class="ts-log-title">{g.call.title || 'Tool'}</span>
      <span class="ts-log-status">{statusLabel(g)}</span>
    </button>
    {#if isDetailOpen}
      <div class="ts-line-detail">
        <ToolGroup call={g.call} result={g.result} />
      </div>
    {/if}
  </div>
{/snippet}

<div class="tool-stream">
  {#if allSettled}
    <button
      class="tool-stream-summary"
      onclick={toggleExpanded}
      aria-expanded={expanded}
    >
      <span class="ts-icon">{'\u2699'}</span>
      <span class="ts-summary-text">{summaryText}</span>
      <span class="ts-chevron">{expanded ? '\u25BE' : '\u25B8'}</span>
    </button>
    {#if expanded}
      <div class="ts-log-lines" role="list">
        {#each groups as g, idx (g.call.toolCallId || `idx-${idx}`)}
          {@render logLine(g, idx)}
        {/each}
      </div>
    {/if}
  {:else}
    {#if earlierCount > 0}
      <button class="ts-earlier-badge" onclick={toggleShowAllLive} aria-expanded={showAllLive}>
        {showAllLive ? 'Hide' : `${earlierCount} earlier`}
      </button>
      {#if showAllLive}
        <div class="ts-log-lines" role="list">
          {#each groups.slice(0, visibleLiveStart) as g, idx (g.call.toolCallId || `idx-${idx}`)}
            {@render logLine(g, idx)}
          {/each}
        </div>
      {/if}
    {/if}
    <div class="ts-log-lines" role="list">
      {#each groups.slice(visibleLiveStart) as g, idx (g.call.toolCallId || `live-${visibleLiveStart + idx}`)}
        {@render logLine(g, visibleLiveStart + idx)}
      {/each}
    </div>
  {/if}
</div>

<style>
  .tool-stream {
    max-width: 90%;
  }

  .tool-stream-summary {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    width: 100%;
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.15);
    font-size: 0.75rem;
    color: hsl(var(--foreground));
    cursor: pointer;
    text-align: left;
    transition: background 0.15s;
  }

  .tool-stream-summary:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .ts-icon {
    flex-shrink: 0;
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .ts-summary-text {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ts-chevron {
    flex-shrink: 0;
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
  }

  .ts-log-lines {
    display: flex;
    flex-direction: column;
    margin-top: 0.25rem;
  }

  .ts-log-line {
    border-left: 2px solid hsl(var(--border));
    margin-left: 0.5rem;
  }

  .ts-log-line-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    width: 100%;
    padding: 0.1875rem 0.5rem;
    background: none;
    border: none;
    font-size: 0.6875rem;
    color: hsl(var(--foreground));
    cursor: pointer;
    text-align: left;
  }

  .ts-log-line-header:hover {
    background: hsl(var(--muted) / 0.15);
  }

  .ts-status-indicator {
    flex-shrink: 0;
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
  }

  .ts-status-indicator.ts-success {
    color: hsl(var(--status-success));
  }

  .ts-status-indicator.ts-error {
    color: hsl(var(--status-danger));
  }

  .ts-status-indicator.ts-running {
    color: hsl(var(--status-working));
  }

  .ts-log-title {
    flex: 1;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ts-log-status {
    flex-shrink: 0;
    font-size: 0.5625rem;
    font-weight: 600;
    padding: 0.0625rem 0.3125rem;
    border-radius: 0.1875rem;
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .ts-line-detail {
    padding: 0.25rem 0.5rem 0.375rem;
  }

  .ts-line-detail :global(.tool-group),
  .ts-line-detail :global(.tool-group-completed),
  .ts-line-detail :global(.tool-group-error),
  .ts-line-detail :global(.tool-group-running) {
    border: none;
    background: transparent;
    padding: 0;
    max-width: 100%;
  }

  .ts-earlier-badge {
    display: inline-flex;
    align-items: center;
    padding: 0.125rem 0.5rem;
    margin-bottom: 0.25rem;
    border-radius: 0.75rem;
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.2);
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
  }

  .ts-earlier-badge:hover {
    background: hsl(var(--muted) / 0.4);
  }
</style>
