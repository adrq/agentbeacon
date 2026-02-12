<script lang="ts">
  import type { Execution } from '../types';
  import { selectedExecutionId } from '../stores/appState';
  import { router } from '../router';

  interface Props {
    execution: Execution;
  }

  let { execution }: Props = $props();

  let selected = $derived($selectedExecutionId === execution.id);
  let needsInput = $derived(execution.status === 'input-required');
  let displayTitle = $derived(execution.title || execution.id.slice(0, 8));
  let statusText = $derived(needsInput ? 'needs answer'
    : execution.status === 'working' ? 'working'
    : execution.status === 'submitted' ? 'submitted'
    : execution.status === 'completed' ? 'completed'
    : execution.status === 'failed' ? 'failed'
    : 'canceled');

  function relativeTime(iso: string): string {
    const diff = Date.now() - new Date(iso).getTime();
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  }

  function handleClick() {
    router.navigate(`/execution/${execution.id}`);
  }
</script>

<button
  class="exec-item"
  class:selected
  class:needs-input={needsInput}
  onclick={handleClick}
  aria-current={selected || undefined}
>
  <div class="exec-item-top">
    <span class="status-indicator" class:working={execution.status === 'working'} class:completed={execution.status === 'completed'} class:failed={execution.status === 'failed'} class:input-required={needsInput} class:submitted={execution.status === 'submitted'} class:canceled={execution.status === 'canceled'}>
      {#if needsInput}!{/if}
    </span>
    <span class="exec-title">{displayTitle}</span>
    <span class="exec-time">{relativeTime(execution.updated_at)}</span>
  </div>
  <div class="exec-item-bottom">
    <span class="exec-status">{statusText}</span>
  </div>
</button>

<style>
  .exec-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.625rem 0.75rem;
    border: none;
    background: transparent;
    cursor: pointer;
    border-left: 3px solid transparent;
    transition: background 0.1s;
  }

  .exec-item:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .exec-item.selected {
    background: hsl(var(--muted));
    border-left-color: hsl(var(--primary));
  }

  .exec-item.needs-input {
    border-left-color: hsl(var(--status-attention));
  }

  .exec-item-top {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .status-indicator {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0;
  }

  .status-indicator.working { background: hsl(var(--status-working)); }
  .status-indicator.completed { background: hsl(var(--status-success)); }
  .status-indicator.failed { background: hsl(var(--status-danger)); }
  .status-indicator.submitted { background: hsl(var(--muted-foreground)); }
  .status-indicator.canceled { background: hsl(var(--muted-foreground)); }

  .status-indicator.input-required {
    background: hsl(var(--status-attention));
    width: 1rem;
    height: 1rem;
    font-size: 0.625rem;
    font-weight: 700;
    color: white;
  }

  .exec-title {
    flex: 1;
    font-size: 0.8125rem;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: hsl(var(--foreground));
  }

  .exec-time {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .exec-item-bottom {
    margin-top: 0.125rem;
    padding-left: 1rem;
  }

  .exec-status {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .needs-input .exec-status {
    color: hsl(var(--status-attention));
    font-weight: 500;
  }
</style>
