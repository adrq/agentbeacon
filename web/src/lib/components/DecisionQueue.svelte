<script lang="ts">
  import { visibleDecisionItems, suppressSession } from '../stores/questionState';
  import DecisionCard from './DecisionCard.svelte';

  let items = $derived($visibleDecisionItems);

  function handleSubmitted(sessionId: string, batchId: string) {
    suppressSession(sessionId, batchId);
  }
</script>

<div class="decision-queue">
  {#if items.length === 0}
    <div class="queue-empty">
      <span class="queue-empty-icon">&#x2713;</span>
      <span>No questions pending</span>
    </div>
  {:else}
    <div class="queue-list">
      {#each items as item (item.sessionId + ':' + item.batchId)}
        <DecisionCard
          sessionId={item.sessionId}
          executionId={item.executionId}
          executionTitle={item.executionTitle}
          agentName={item.agentName}
          batchId={item.batchId}
          questions={item.questions}
          createdAt={item.createdAt}
          onsubmitted={handleSubmitted}
        />
      {/each}
    </div>
  {/if}
</div>

<style>
  .decision-queue {
    padding: 0.75rem;
  }

  .queue-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    padding: 1.5rem;
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
    border: 1px dashed hsl(var(--border));
    border-radius: 0.5rem;
  }

  .queue-empty-icon {
    color: hsl(var(--status-success));
    font-weight: 700;
  }

  .queue-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
</style>
