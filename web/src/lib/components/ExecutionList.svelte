<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import type { Execution } from '../types';
  import { api } from '../api';
  import { router } from '../router';
  import { selectedExecutionId } from '../stores/appState';
  import ExecutionListItem from './ExecutionListItem.svelte';

  let executions: Execution[] = $state([]);
  let loading = $state(true);
  let error: string | null = $state(null);
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let didAutoSelect = false;

  const statusOrder: Record<string, number> = {
    'input-required': 0,
    'working': 1,
    'submitted': 2,
    'completed': 3,
    'failed': 4,
    'canceled': 5,
  };

  let sorted = $derived([...executions].sort((a, b) => {
    const orderDiff = (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9);
    if (orderDiff !== 0) return orderDiff;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  }));

  let inputRequiredCount = $derived(executions.filter(e => e.status === 'input-required').length);

  async function fetchExecutions() {
    try {
      executions = await api.getExecutions();
      error = null;

      if (!didAutoSelect && !$selectedExecutionId) {
        didAutoSelect = true;
        const firstInput = executions.find(e => e.status === 'input-required');
        if (firstInput) {
          router.navigate(`/execution/${firstInput.id}`);
        }
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load';
    } finally {
      loading = false;
    }
  }

  function handleAttentionClick() {
    const first = sorted.find(e => e.status === 'input-required');
    if (first) router.navigate(`/execution/${first.id}`);
  }

  onMount(() => {
    fetchExecutions();
    pollTimer = setInterval(fetchExecutions, 5000);
  });

  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer);
  });
</script>

<div class="exec-list scroll-thin">
  {#if inputRequiredCount > 0}
    <button class="attention-banner" onclick={handleAttentionClick} aria-label="Jump to first execution needing answers">
      <span class="attention-icon" aria-hidden="true">!</span>
      <span>{inputRequiredCount} question{inputRequiredCount > 1 ? 's' : ''} waiting</span>
    </button>
  {/if}

  {#if loading}
    <div class="list-message">Loading...</div>
  {:else if error}
    <div class="list-message list-error">{error}</div>
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
    color: white;
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
    background: white;
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
