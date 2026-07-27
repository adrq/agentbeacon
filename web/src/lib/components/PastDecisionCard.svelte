<script lang="ts">
  import type { DecisionBatch } from '../stores/questionState';
  import { router } from '../router';
  import ElapsedTime from './ElapsedTime.svelte';

  interface Props {
    item: DecisionBatch;
  }

  let { item }: Props = $props();
  let expanded = $state(false);
</script>

<div class="past-card">
  <button type="button" class="past-header" onclick={() => expanded = !expanded} aria-expanded={expanded}>
    <span class="past-status-icon" class:answered={item.status === 'answered'} class:dismissed={item.status === 'dismissed' || item.status === 'expired'}>
      {item.status === 'answered' ? '\u2713' : '\u2715'}
    </span>
    <span class="past-title">{item.executionTitle ?? 'Untitled'}</span>
    <span class="past-time">
      <ElapsedTime startTime={item.answeredAt ?? item.dismissedAt ?? item.createdAt} /> ago
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
      {#each item.questions as q, i}
        <div class="past-question-block">
          {#if item.questions.length > 1}
            <span class="q-index">Q{i + 1}.</span>
          {/if}
          <span class="q-text">{q.questionText}</span>
        </div>
        {#if q.options?.length}
          <div class="past-options">
            {#each q.options as opt}
              <div class="past-option">
                <span class="opt-label">{opt.label}</span>
                {#if opt.description}
                  <span class="opt-desc">{opt.description}</span>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      {/each}

      <div class="past-resolution">
        {#if item.status === 'answered'}
          {#if item.answer}
            <span class="resolution-label answered-label">Answer:</span>
            <span class="resolution-value">{item.answer}</span>
          {/if}
          {#if item.truncated}
            <span class="truncated-note" title="This answer was shortened when migrated to the new decisions format.">answer truncated during migration</span>
          {/if}
        {:else if item.status === 'dismissed'}
          <span class="resolution-label dismissed-label">Dismissed</span>
        {:else if item.status === 'expired'}
          <span class="resolution-label dismissed-label">Expired</span>
        {/if}
      </div>
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

  .past-status-icon.answered { color: hsl(var(--status-success)); }
  .past-status-icon.dismissed { color: hsl(var(--muted-foreground)); }

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

  .past-options {
    display: flex;
    flex-direction: column;
    gap: 0.125rem;
    padding-left: 0.5rem;
  }

  .past-option {
    display: flex;
    align-items: baseline;
    gap: 0.375rem;
    font-size: 13px;
  }

  .opt-label {
    font-weight: 500;
    color: hsl(var(--foreground));
  }

  .opt-desc {
    font-size: 11px;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .past-resolution {
    padding-top: 0.25rem;
    border-top: 1px solid hsl(var(--border) / 0.5);
  }

  .resolution-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.02em;
  }

  .answered-label {
    color: hsl(var(--status-success));
  }

  .truncated-note {
    display: block;
    margin-top: 0.15rem;
    font-size: 0.72rem;
    font-style: italic;
    color: hsl(var(--muted-foreground));
  }

  .dismissed-label {
    color: hsl(var(--muted-foreground));
    font-style: italic;
  }

  .resolution-value {
    font-size: 13px;
    font-weight: 500;
    color: hsl(var(--foreground));
    margin-left: 0.25rem;
  }
</style>
