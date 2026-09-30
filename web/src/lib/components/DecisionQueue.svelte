<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import {
    pendingDecisions,
    pastDecisions,
    pastDecisionsHasMore,
    pastDecisionsError,
    refreshDecisions,
    loadMorePastDecisions,
  } from '../stores/questionState';
  import { api, ApiError } from '../api';
  import { router } from '../router';
  import { toasts } from '../stores/toasts';
  import DecisionCard from './DecisionCard.svelte';
  import PastDecisionCard from './PastDecisionCard.svelte';
  import ElapsedTime from './ElapsedTime.svelte';

  let pending = $derived($pendingDecisions);
  let past = $derived($pastDecisions);
  let hasMorePast = $derived($pastDecisionsHasMore);
  let historyError = $derived($pastDecisionsError);
  let retryingHistory = $state(false);

  // Retries the initial history read.
  async function retryHistory() {
    if (retryingHistory) return;
    retryingHistory = true;
    try {
      await loadMorePastDecisions(true);
    } catch {
      // The error state is already set.
    } finally {
      retryingHistory = false;
    }
  }
  let loadingMore = $state(false);
  let loadMoreError: string | null = $state(null);

  // Walks the resolved history one page at a time from where the last stopped.
  async function loadMorePast() {
    if (loadingMore) return;
    loadingMore = true;
    loadMoreError = null;
    try {
      await loadMorePastDecisions(false);
    } catch (e) {
      loadMoreError = e instanceof Error ? e.message : 'Failed to load more';
    } finally {
      loadingMore = false;
    }
  }

  async function handleDismiss(eventId: string) {
    try {
      await api.dismissDecision(eventId);
      await refreshDecisions();
    } catch (e) {
      // Compare the reported resolution against what was requested.
      if (e instanceof ApiError && e.code === 'decision.already_resolved') {
        const kind = (e.problem?.resolution as { kind?: string } | undefined)?.kind;
        await refreshDecisions();
        if (kind === 'dismissed') return;
        toasts.error('That decision was answered from another device');
        return;
      }
      toasts.error(e instanceof Error ? e.message : 'Failed to dismiss');
    }
  }
</script>

<div class="decision-queue">
  {#if pending.length === 0 && past.length === 0 && !hasMorePast && !historyError}
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
          {#each pending as item (item.eventId)}
            <DecisionCard
              sessionId={item.sessionId}
              executionId={item.executionId}
              executionTitle={item.executionTitle}
              projectName={null}
              eventId={item.eventId}
              questions={item.questions}
              createdAt={item.createdAt}
              ondismiss={() => handleDismiss(item.eventId)}
            />
          {/each}
        </div>
      </div>
    {/if}

    <!-- A page can come back empty with more behind it, so the continuation
         control is shown whenever the walk has somewhere left to go. -->
    {#if historyError}
      <div class="section">
        <div class="section-header">Past Decisions</div>
        <div class="load-more-error" role="alert">{historyError}</div>
        <button
          type="button"
          class="load-more-btn"
          data-testid="past-retry"
          disabled={retryingHistory}
          onclick={retryHistory}
        >
          {retryingHistory ? 'Retrying…' : 'Retry loading history'}
        </button>
      </div>
    {:else if past.length > 0 || hasMorePast}
      <div class="section">
        <div class="section-header">
          Past Decisions{past.length > 0 ? ` (${past.length})` : ''}
        </div>
        {#if past.length > 0}
          <div class="past-list">
            {#each past as item (item.eventId)}
              <PastDecisionCard {item} />
            {/each}
          </div>
        {:else}
          <div class="past-empty" role="status">No resolved decisions loaded yet</div>
        {/if}
        {#if hasMorePast}
          <button
            type="button"
            class="load-more-btn"
            data-testid="past-load-more"
            disabled={loadingMore}
            onclick={loadMorePast}
          >
            {loadingMore ? 'Loading…' : 'Load older decisions'}
          </button>
        {/if}
        {#if loadMoreError}
          <div class="load-more-error" role="alert">{loadMoreError}</div>
        {/if}
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

  .past-empty {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground) / 0.8);
    padding: 0.25rem 0;
  }

  .load-more-btn {
    width: 100%;
    margin-top: 0.375rem;
    padding: 0.25rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    color: hsl(var(--muted-foreground));
    font-size: 0.6875rem;
    font-weight: 500;
    cursor: pointer;
  }

  .load-more-btn:hover:not(:disabled) {
    background: hsl(var(--muted) / 0.3);
    color: hsl(var(--foreground));
  }

  .load-more-btn:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .load-more-error {
    margin-top: 0.375rem;
    padding: 0.25rem 0.5rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.6875rem;
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
