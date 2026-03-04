<script lang="ts">
  import type { Execution } from '../types';
  import { executionsWithQuestions } from '../stores/questionState';
  import { homeFeedFilter, type HomeFeedFilter } from '../stores/appState';
  import { router } from '../router';

  interface FeedItem {
    id: string;
    icon: string;
    iconClass: string;
    title: string;
    verb: string;
    timeAgo: string;
    status: string;
  }

  interface Props {
    executions: Execution[];
  }

  let { executions }: Props = $props();

  function relativeTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    return `${days}d`;
  }

  const statusConfig: Record<string, { icon: string; iconClass: string }> = {
    'working': { icon: '\u25CF', iconClass: 'working' },
    'completed': { icon: '\u2713', iconClass: 'completed' },
    'failed': { icon: '\u2717', iconClass: 'failed' },
    'input-required': { icon: '!', iconClass: 'attention' },
    'submitted': { icon: '\u25CF', iconClass: 'muted' },
    'canceled': { icon: '\u2014', iconClass: 'muted' },
  };

  const verbMap: Record<string, string> = {
    'working': 'is working',
    'completed': 'completed',
    'failed': 'failed',
    'input-required': 'is awaiting input',
    'submitted': 'was submitted',
    'canceled': 'was canceled',
  };

  const DAY_MS = 86_400_000;

  function matchesFilter(exec: Execution, filter: HomeFeedFilter): boolean {
    if (!filter) return true;
    switch (filter) {
      case 'running': return exec.status === 'working';
      case 'waiting':
        return exec.status === 'input-required' && $executionsWithQuestions.has(exec.id);
      case 'completed':
        return exec.status === 'completed' && !!exec.completed_at &&
          Date.now() - new Date(exec.completed_at).getTime() < DAY_MS;
      case 'failed':
        return exec.status === 'failed' && !!exec.completed_at &&
          Date.now() - new Date(exec.completed_at).getTime() < DAY_MS;
      default: return true;
    }
  }

  let feedItems = $derived(
    [...executions]
      .filter(exec => matchesFilter(exec, $homeFeedFilter))
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 20)
      .map((exec): FeedItem => {
        const cfg = statusConfig[exec.status] ?? { icon: '\u25CF', iconClass: 'muted' };
        return {
          id: exec.id,
          icon: cfg.icon,
          iconClass: cfg.iconClass,
          title: exec.title ?? exec.id.slice(0, 8),
          verb: verbMap[exec.status] ?? exec.status,
          timeAgo: relativeTime(exec.updated_at),
          status: exec.status,
        };
      })
  );

  function handleClick(id: string) {
    router.navigate(`/execution/${id}`);
  }
</script>

<div class="activity-feed">
  <div class="feed-header section-heading">
    Activity
    {#if $homeFeedFilter}
      <button class="filter-clear" onclick={() => homeFeedFilter.set(null)}>
        Clear filter
      </button>
    {/if}
  </div>
  {#if feedItems.length > 0}
    <div class="feed-list">
      {#each feedItems as item (item.id)}
        <button class="feed-item" onclick={() => handleClick(item.id)}>
          <span class="feed-icon {item.iconClass}">{item.icon}</span>
          <span class="feed-title">
            <span class="feed-exec-name">{item.title}</span>
            <span class="feed-verb">{item.verb}</span>
          </span>
          <span class="feed-time">{item.timeAgo}</span>
        </button>
      {/each}
    </div>
  {:else if $homeFeedFilter}
    <div class="feed-empty">No matching executions</div>
  {/if}
</div>

<style>
  .activity-feed {
    padding: 0.5rem 1rem 1rem;
  }

  .feed-header {
    margin-bottom: 0.5rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .filter-clear {
    font-size: var(--text-xs);
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    background: none;
    border: none;
    cursor: pointer;
    padding: 0;
    text-decoration: none;
  }

  .filter-clear:hover {
    text-decoration: underline;
  }

  .feed-list {
    display: flex;
    flex-direction: column;
  }

  .feed-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.5rem;
    border: none;
    background: transparent;
    cursor: pointer;
    border-radius: var(--radius-sm);
    transition: background 0.1s;
    text-align: left;
    width: 100%;
  }

  .feed-item:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .feed-icon {
    width: 1rem;
    text-align: center;
    flex-shrink: 0;
    font-size: var(--text-xs);
    font-weight: 500;
  }

  .feed-icon.working { color: hsl(var(--status-working)); }
  .feed-icon.completed { color: hsl(var(--status-success)); }
  .feed-icon.failed { color: hsl(var(--status-danger)); }
  .feed-icon.attention { color: hsl(var(--status-attention)); }
  .feed-icon.muted { color: hsl(var(--muted-foreground)); }

  .feed-title {
    flex: 1;
    font-size: var(--text-sm);
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    display: flex;
    align-items: baseline;
    gap: 0.375rem;
  }

  .feed-exec-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .feed-verb {
    color: hsl(var(--muted-foreground));
    font-size: var(--text-xs);
    font-weight: 500;
    flex-shrink: 0;
  }

  .feed-time {
    font-size: var(--text-xs);
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }

  .feed-empty {
    padding: 1.5rem 0.5rem;
    text-align: center;
    color: hsl(var(--muted-foreground));
    font-size: var(--text-sm);
  }
</style>
