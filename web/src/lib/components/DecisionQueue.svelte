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
    <div class="queue-empty" role="status">
      <span class="pulse-dot"></span>
      <span class="queue-empty-title">No pending decisions</span>
      <span class="queue-empty-subtitle">Agents operating autonomously</span>
    </div>
  {:else}
    <div class="queue-list">
      {#each items as item (item.sessionId + ':' + item.batchId)}
        <DecisionCard
          sessionId={item.sessionId}
          executionId={item.executionId}
          executionTitle={item.executionTitle}
          agentName={item.agentName}
          projectName={item.projectName}
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

  .queue-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
</style>
