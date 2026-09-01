<script lang="ts">
  import type { DecisionBatch, DecisionSummary } from '../stores/questionState';
  import { fetchDecisionDetail } from '../stores/questionState';
  import { router } from '../router';
  import ElapsedTime from './ElapsedTime.svelte';

  interface Props {
    item: DecisionSummary;
  }

  let { item }: Props = $props();
  let expanded = $state(false);
  // The decision's kind, timestamp and answer text, fetched on expansion.
  let detail: DecisionBatch | null = $state(null);
  let loading = $state(false);
  let error: string | null = $state(null);

  // A history card can hold a decision whose detail reports pending.
  const STATUS_ICON = {
    answered: '\u2713',
    dismissed: '\u2715',
    expired: '\u25CB',
    pending: '\u25CF',
  };
  const STATUS_LABEL = {
    answered: 'Answered',
    dismissed: 'Dismissed',
    expired: 'Expired',
    pending: 'Awaiting an answer',
  };

  async function loadDetail() {
    if (detail || loading) return;
    loading = true;
    error = null;
    try {
      detail = await fetchDecisionDetail(item.eventId);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load decision';
    } finally {
      loading = false;
    }
  }

  function toggle() {
    expanded = !expanded;
    if (expanded) void loadDetail();
  }

</script>

<div class="past-card">
  <button type="button" class="past-header" onclick={toggle} aria-expanded={expanded}>
    <span class="past-status-icon {detail?.status ?? 'resolved'}" data-testid="past-status-icon">
      {detail ? STATUS_ICON[detail.status as keyof typeof STATUS_ICON] ?? '\u2713' : '\u2713'}
    </span>
    <span class="past-title">{item.executionTitle ?? 'Untitled'}</span>
    <span class="past-time">
      <ElapsedTime startTime={item.createdAt} /> ago
    </span>
    <span class="expand-toggle">
      <span class="expand-chevron" class:open={expanded} aria-hidden="true">&#x25B8;</span>
    </span>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <span class="past-view-link" role="link" tabindex="0" onclick={(e: MouseEvent) => { e.stopPropagation(); router.navigate(`/execution/${item.executionId}`); }} onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') { e.stopPropagation(); router.navigate(`/execution/${item.executionId}`); } }}>
      view execution &rarr;
    </span>
  </button>

  {#if expanded}
    <div class="past-detail">
      <div class="past-question-block">
        <span class="q-text">{item.questionPreview}</span>
      </div>
      {#if item.questionCount > 1}
        <div class="past-question-block">
          <span class="q-index">+{item.questionCount - 1} more</span>
        </div>
      {/if}

      {#if loading}
        <div class="past-resolution" role="status" data-testid="past-detail-loading">Loading&hellip;</div>
      {:else if error}
        <div class="card-error" role="alert" data-testid="past-detail-error">{error}</div>
      {:else if detail}
        {@const resolvedAt = detail.answeredAt ?? detail.dismissedAt}
        <div class="past-resolution" data-testid="past-resolution">
          <span class="resolution-kind {detail.status}">
            {STATUS_LABEL[detail.status as keyof typeof STATUS_LABEL] ?? detail.status}
          </span>
          {#if resolvedAt}
            <span class="resolution-time"><ElapsedTime startTime={resolvedAt} /> ago</span>
          {/if}
        </div>
        {#if detail.status === 'answered'}
          {#if detail.answer}
            <div class="past-answer" data-testid="past-answer">{detail.answer}</div>
            {#if detail.truncated}
              <div class="resolution-note">Answer truncated for storage</div>
            {/if}
          {:else}
            <div class="resolution-note">Answered without text</div>
          {/if}
        {:else if detail.status === 'expired'}
          <div class="resolution-note">The execution ended before this was answered</div>
        {:else if detail.status === 'pending'}
          <div class="resolution-note" data-testid="past-detail-pending">
            This decision is open again and is answered from its execution.
          </div>
          <button
            type="button"
            class="past-goto-live"
            data-testid="past-goto-live"
            onclick={() => router.navigate(`/execution/${item.executionId}`)}
          >
            Go to the open decision &rarr;
          </button>
        {/if}
      {/if}
    </div>
  {/if}
</div>

<style>
  .past-card {
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    overflow: hidden;
  }

  .past-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.375rem 0.5rem;
    width: 100%;
    border: none;
    background: none;
    font: inherit;
    color: inherit;
    text-align: left;
    cursor: pointer;
  }

  .past-header:hover {
    background: hsl(var(--muted) / 0.2);
  }

  .past-status-icon {
    font-size: 0.75rem;
    font-weight: 600;
    flex-shrink: 0;
  }

  .past-status-icon.answered,
  .past-status-icon.resolved { color: hsl(var(--status-success)); }
  .past-status-icon.dismissed,
  .past-status-icon.expired { color: hsl(var(--muted-foreground)); }

  .past-title {
    font-size: 0.75rem;
    font-weight: 500;
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  .past-time {
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .expand-toggle {
    flex-shrink: 0;
    font-size: 0.5rem;
    color: hsl(var(--muted-foreground));
  }

  .expand-chevron {
    display: inline-block;
    transition: transform 0.15s ease;
  }

  .expand-chevron.open {
    transform: rotate(90deg);
  }

  .past-view-link {
    font-size: 11px;
    font-weight: 500;
    color: hsl(var(--primary));
    cursor: pointer;
    flex-shrink: 0;
    white-space: nowrap;
  }

  .past-view-link:hover {
    text-decoration: underline;
  }

  .past-detail {
    padding: 0.375rem 0.5rem 0.5rem;
    border-top: 1px solid hsl(var(--border));
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
  }

  .past-question-block {
    font-size: 13px;
    color: hsl(var(--foreground));
    line-height: 1.4;
  }

  .q-index {
    font-weight: 600;
    margin-right: 0.25rem;
    color: hsl(var(--muted-foreground));
  }

  .q-text {
    font-weight: 400;
  }

  .past-resolution {
    display: flex;
    align-items: baseline;
    gap: 0.375rem;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .resolution-kind {
    font-weight: 600;
  }

  .resolution-kind.answered { color: hsl(var(--status-success)); }

  .past-goto-live {
    margin-top: 0.25rem;
    padding: 0;
    border: none;
    background: none;
    font: inherit;
    font-size: 0.625rem;
    color: hsl(var(--primary));
    cursor: pointer;
  }

  .resolution-time {
    font-size: 0.625rem;
  }

  .past-answer {
    font-size: 13px;
    line-height: 1.4;
    color: hsl(var(--foreground));
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .resolution-note {
    font-size: 0.6875rem;
    font-style: italic;
    color: hsl(var(--muted-foreground));
  }

  .card-error {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.6875rem;
  }










</style>
