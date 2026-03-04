<script lang="ts">
  import { get } from 'svelte/store';
  import type { Execution } from '../types';
  import { executionsWithQuestions } from '../stores/questionState';
  import { homeFeedFilter, type HomeFeedFilter, actionPanelCollapsed, userExplicitlyCollapsed } from '../stores/appState';

  interface Props {
    executions: Execution[];
  }

  let { executions }: Props = $props();

  const DAY_MS = 86_400_000;

  let running = $derived(executions.filter(e => e.status === 'working').length);

  let waiting = $derived(
    executions.filter(e =>
      e.status === 'input-required' && $executionsWithQuestions.has(e.id)
    ).length
  );

  let completed24h = $derived(
    executions.filter(e =>
      e.status === 'completed' && e.completed_at &&
      Date.now() - new Date(e.completed_at).getTime() < DAY_MS
    ).length
  );

  let failed24h = $derived(
    executions.filter(e =>
      e.status === 'failed' && e.completed_at &&
      Date.now() - new Date(e.completed_at).getTime() < DAY_MS
    ).length
  );

  function handleTileClick(filter: HomeFeedFilter) {
    const wasActive = get(homeFeedFilter) === filter;
    homeFeedFilter.update(current => current === filter ? null : filter);
    if (filter === 'waiting' && waiting > 0 && !wasActive) {
      actionPanelCollapsed.set(false);
      userExplicitlyCollapsed.set(false);
    }
  }

  let tiles = $derived([
    { key: 'running' as const, count: running, label: 'Running', icon: '\u25CF' },
    { key: 'waiting' as const, count: waiting, label: 'Waiting', icon: '\u25C9' },
    { key: 'completed' as const, count: completed24h, label: 'Done 24h', icon: '\u2713' },
    { key: 'failed' as const, count: failed24h, label: 'Failed 24h', icon: '\u2717' },
  ]);
</script>

<div class="ops-summary">
  {#each tiles as tile (tile.key)}
    <button
      class="tile {tile.key}"
      class:active={$homeFeedFilter === tile.key}
      class:urgent={tile.key === 'waiting' && tile.count > 0}
      aria-pressed={$homeFeedFilter === tile.key}
      onclick={() => handleTileClick(tile.key)}
    >
      <span class="tile-count">{tile.count}</span>
      <span class="tile-label">{tile.label}</span>
    </button>
  {/each}
</div>

<style>
  .ops-summary {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 0.5rem;
    padding: 0.75rem 1rem 0.25rem;
  }

  .tile {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.125rem;
    padding: 0.625rem 0.75rem;
    border: 1px solid transparent;
    border-radius: var(--radius);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s, box-shadow 0.15s;
  }

  .tile.running  { background: hsl(var(--status-working) / 0.1); }
  .tile.waiting  { background: hsl(var(--status-attention) / 0.1); }
  .tile.completed { background: hsl(var(--status-success) / 0.1); }
  .tile.failed   { background: hsl(var(--status-danger) / 0.1); }

  .tile.running:hover  { background: hsl(var(--status-working) / 0.15); }
  .tile.waiting:hover  { background: hsl(var(--status-attention) / 0.15); }
  .tile.completed:hover { background: hsl(var(--status-success) / 0.15); }
  .tile.failed:hover   { background: hsl(var(--status-danger) / 0.15); }

  .tile.running.active  { background: hsl(var(--status-working) / 0.2); border-color: hsl(var(--status-working) / 0.4); }
  .tile.waiting.active  { background: hsl(var(--status-attention) / 0.2); border-color: hsl(var(--status-attention) / 0.4); }
  .tile.completed.active { background: hsl(var(--status-success) / 0.2); border-color: hsl(var(--status-success) / 0.4); }
  .tile.failed.active   { background: hsl(var(--status-danger) / 0.2); border-color: hsl(var(--status-danger) / 0.4); }

  .tile.urgent {
    box-shadow: 0 0 8px 2px hsl(var(--status-attention) / 0.3);
    animation: urgency-pulse 3s ease-in-out infinite;
  }

  @keyframes urgency-pulse {
    0%, 100% { box-shadow: 0 0 4px 1px hsl(var(--status-attention) / 0.2); }
    50% { box-shadow: 0 0 12px 4px hsl(var(--status-attention) / 0.4); }
  }

  .tile-count {
    font-size: var(--text-lg);
    font-weight: 600;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }

  .tile.running  .tile-count { color: hsl(var(--status-working)); }
  .tile.waiting  .tile-count { color: hsl(var(--status-attention)); }
  .tile.completed .tile-count { color: hsl(var(--status-success)); }
  .tile.failed   .tile-count { color: hsl(var(--status-danger)); }

  .tile-label {
    font-size: var(--text-xs);
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: hsl(var(--muted-foreground));
  }

  @media (max-width: 500px) {
    .ops-summary {
      grid-template-columns: repeat(2, 1fr);
    }
  }
</style>
