<script lang="ts">
  import { pendingDecisions, pastDecisions, refreshDecisions } from '../stores/questionState';
  import { api } from '../api';
  import { router } from '../router';
  import { toasts } from '../stores/toasts';
  import DecisionCard from './DecisionCard.svelte';
  import PastDecisionCard from './PastDecisionCard.svelte';
  import ElapsedTime from './ElapsedTime.svelte';

  let pending = $derived($pendingDecisions);
  let past = $derived($pastDecisions);

  async function handleDismiss(batchId: string) {
    try {
      await api.dismissBatch(batchId);
      await refreshDecisions();
    } catch (e) {
      toasts.error(e instanceof Error ? e.message : 'Failed to dismiss');
    }
  }
</script>

<div class="decision-queue">
  {#if pending.length === 0 && past.length === 0}
    <div class="queue-empty" role="status">
      <span class="pulse-dot"></span>
      <span class="queue-empty-title">No pending decisions</span>
      <span class="queue-empty-subtitle">Agents operating autonomously</span>
    </div>
  {:else}
    {#if pending.length > 0}
      <div class="section">
        <div class="section-header">Pending ({pending.length})</div>
        <div class="queue-list">
          {#each pending as item (item.batchId)}
            <DecisionCard
              sessionId={item.sessionId}
              executionId={item.executionId}
              executionTitle={item.executionTitle}
              agentName={item.agentName}
              projectName={null}
              batchId={item.batchId}
              questions={item.questions}
              createdAt={item.createdAt}
              ondismiss={() => handleDismiss(item.batchId)}
            />
          {/each}
        </div>
      </div>
    {/if}

    {#if past.length > 0}
      <div class="section">
        <div class="section-header">Past Decisions ({past.length})</div>
        <div class="past-list">
          {#each past as item (item.batchId)}
            <PastDecisionCard {item} />
          {/each}
        </div>
      </div>
    {/if}
  {/if}
</div>

<style>
  .decision-queue {
    display: flex;
    flex-direction: column;
    flex: 1;
    padding: 0.5rem;
  }

  .queue-empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
  }

  .pulse-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: hsl(var(--status-success));
    animation: standing-by 4s ease-in-out infinite;
  }

  @keyframes standing-by {
    0%, 100% { box-shadow: 0 0 4px 1px hsl(var(--status-success) / 0.2); }
    50% { box-shadow: 0 0 12px 4px hsl(var(--status-success) / 0.4); }
  }

  @media (prefers-reduced-motion: reduce) {
    .pulse-dot { animation: none; }
  }

  .queue-empty-title {
    font-size: var(--text-sm);
    font-weight: 400;
    color: hsl(var(--muted-foreground));
  }

  .queue-empty-subtitle {
    font-size: var(--text-xs);
    color: hsl(var(--muted-foreground) / 0.7);
  }

  .section {
    margin-bottom: 0.75rem;
  }

  .section-header {
    font-size: 0.6875rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    color: hsl(var(--muted-foreground));
    margin-bottom: 0.375rem;
  }

  .queue-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .past-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
</style>
