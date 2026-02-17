<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { executions, executionsLoading, executionsError, inputRequiredCount, startPolling, stopPolling } from '../stores/executions';
  import { router } from '../router';
  import ExecutionListItem from './ExecutionListItem.svelte';

  const statusOrder: Record<string, number> = {
    'input-required': 0,
    'working': 1,
    'submitted': 2,
    'completed': 3,
    'failed': 4,
    'canceled': 5,
  };

  let sorted = $derived([...$executions].sort((a, b) => {
    const orderDiff = (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9);
    if (orderDiff !== 0) return orderDiff;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  }));

  function handleAttentionClick() {
    const first = sorted.find(e => e.status === 'input-required');
    if (first) router.navigate(`/execution/${first.id}`);
  }

  onMount(() => startPolling());
  onDestroy(() => stopPolling());
</script>

<div class="exec-list scroll-thin">
  {#if $inputRequiredCount > 0}
    <button class="attention-banner" onclick={handleAttentionClick} aria-label="Jump to first execution needing answers">
      <span class="attention-icon" aria-hidden="true">!</span>
      <span>{$inputRequiredCount} question{$inputRequiredCount > 1 ? 's' : ''} waiting</span>
    </button>
  {/if}

  {#if $executionsLoading}
    <div class="list-message">Loading...</div>
  {:else if $executionsError}
    <div class="list-message list-error">{$executionsError}</div>
  {:else if sorted.length === 0}
    <div class="list-message">No executions yet</div>
  {:else}
    {#each sorted as execution (execution.id)}
      <ExecutionListItem {execution} />
    {/each}
  {/if}
</div>

<style>
  .exec-list {
    height: 100%;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .attention-banner {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    margin: 0.5rem;
    border-radius: 0.375rem;
    background: hsl(var(--status-attention));
    color: hsl(var(--primary-foreground));
    font-size: 0.8125rem;
    font-weight: 600;
    border: none;
    cursor: pointer;
    transition: brightness 0.15s;
  }

  .attention-banner:hover {
    filter: brightness(1.1);
  }

  .attention-icon {
    width: 1.125rem;
    height: 1.125rem;
    border-radius: 50%;
    background: hsl(var(--primary-foreground));
    color: hsl(var(--status-attention));
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.6875rem;
    font-weight: 800;
    flex-shrink: 0;
  }

  .list-message {
    padding: 2rem 1rem;
    text-align: center;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .list-error {
    color: hsl(var(--status-danger));
  }
</style>
