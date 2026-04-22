<script lang="ts">
  import { pendingDecisions, pastDecisions } from '../stores/questionState';
  import { api } from '../api';
  import { router } from '../router';
  import { toasts } from '../stores/toasts';
  import DecisionCard from './DecisionCard.svelte';
  import ElapsedTime from './ElapsedTime.svelte';

  let pending = $derived($pendingDecisions);
  let past = $derived($pastDecisions);

  async function handleDismiss(batchId: string) {
    try {
      await api.dismissBatch(batchId);
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
            <div class="past-card">
              <div class="past-header">
                <span class="past-status-icon" class:answered={item.status === 'answered'} class:dismissed={item.status === 'dismissed'}>
                  {item.status === 'answered' ? '\u2713' : '\u2715'}
                </span>
                <span class="past-question">{item.questions[0]?.questionText ?? 'Question'}</span>
              </div>
              <div class="past-meta">
                {#if item.status === 'answered' && item.answer}
                  <span class="past-answer">Answered: "{item.answer}"</span>
                {:else if item.status === 'dismissed'}
                  <span class="past-dismissed">Dismissed</span>
                {:else if item.status === 'expired'}
                  <span class="past-dismissed">Expired</span>
                {:else}
                  <span class="past-answered">Answered</span>
                {/if}
                <span class="past-time">
                  <ElapsedTime startTime={item.answeredAt ?? item.dismissedAt ?? item.createdAt} />
                  ago
                </span>
                <button type="button" class="past-link" onclick={() => router.navigate(`/execution/${item.executionId}`)}>
                  view execution &rarr;
                </button>
              </div>
            </div>
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

  .past-card {
    padding: 0.375rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
  }

  .past-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
  }

  .past-status-icon {
    font-size: 0.75rem;
    font-weight: 600;
  }

  .past-status-icon.answered {
    color: hsl(var(--status-success));
  }

  .past-status-icon.dismissed {
    color: hsl(var(--muted-foreground));
  }

  .past-question {
    font-size: 0.75rem;
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .past-meta {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    margin-top: 0.125rem;
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
  }

  .past-answer {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 150px;
  }

  .past-dismissed {
    font-style: italic;
  }

  .past-answered {
    color: hsl(var(--status-success));
  }

  .past-time {
    flex-shrink: 0;
  }

  .past-link {
    background: none;
    border: none;
    font: inherit;
    font-size: 0.625rem;
    color: hsl(var(--primary));
    cursor: pointer;
    padding: 0;
    flex-shrink: 0;
  }

  .past-link:hover {
    text-decoration: underline;
  }
</style>
