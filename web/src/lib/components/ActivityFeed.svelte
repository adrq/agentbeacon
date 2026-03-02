<script lang="ts">
  import { executionsQuery } from '../queries/executions';
  import { router } from '../router';

  interface FeedItem {
    id: string;
    icon: string;
    iconClass: string;
    title: string;
    timeAgo: string;
    status: string;
  }

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

  const execsQuery = executionsQuery();

  let feedItems = $derived(
    [...(execsQuery.data ?? [])]
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 20)
      .map((exec): FeedItem => {
        const cfg = statusConfig[exec.status] ?? { icon: '\u25CF', iconClass: 'muted' };
        return {
          id: exec.id,
          icon: cfg.icon,
          iconClass: cfg.iconClass,
          title: exec.title ?? exec.id.slice(0, 8),
          timeAgo: relativeTime(exec.updated_at),
          status: exec.status,
        };
      })
  );

  function handleClick(id: string) {
    router.navigate(`/execution/${id}`);
  }
</script>

{#if feedItems.length > 0}
  <div class="activity-feed">
    <div class="feed-header section-heading">Activity</div>
    <div class="feed-list">
      {#each feedItems as item (item.id)}
        <button class="feed-item" onclick={() => handleClick(item.id)}>
          <span class="feed-icon {item.iconClass}">{item.icon}</span>
          <span class="feed-title">{item.title}</span>
          <span class="feed-time">{item.timeAgo}</span>
        </button>
      {/each}
    </div>
  </div>
{/if}

<style>
  .activity-feed {
    padding: 0.5rem 1rem 1rem;
  }

  .feed-header {
    margin-bottom: 0.5rem;
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
    font-size: 0.6875rem;
    font-weight: 600;
  }

  .feed-icon.working { color: hsl(var(--status-working)); }
  .feed-icon.completed { color: hsl(var(--status-success)); }
  .feed-icon.failed { color: hsl(var(--status-danger)); }
  .feed-icon.attention { color: hsl(var(--status-attention)); }
  .feed-icon.muted { color: hsl(var(--muted-foreground)); }

  .feed-title {
    flex: 1;
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .feed-time {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
    font-family: var(--font-mono);
  }
</style>
