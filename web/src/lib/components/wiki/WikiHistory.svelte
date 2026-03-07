<script lang="ts">
  import { wikiRevisionsQuery, wikiRevisionQuery } from '../../queries/wiki';
  import Markdown from '../Markdown.svelte';

  interface Props {
    projectId: string;
    slug: string;
  }

  let { projectId, slug }: Props = $props();

  const revisionsQuery = wikiRevisionsQuery(() => projectId, () => slug);
  let revisions = $derived(revisionsQuery.data ?? []);

  let selectedRev = $state<number | null>(null);

  const revisionDetail = wikiRevisionQuery(
    () => projectId,
    () => slug,
    () => selectedRev,
  );

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }
</script>

<div class="history-panel">
  {#if selectedRev != null && revisionDetail.isLoading}
    <div class="history-loading">Loading revision...</div>
  {:else if selectedRev != null && revisionDetail.isError}
    <div class="history-error">
      <p>Failed to load revision: {revisionDetail.error?.message ?? 'Unknown error'}</p>
      <button class="back-btn" onclick={() => selectedRev = null}>Back to history</button>
    </div>
  {:else if selectedRev != null && revisionDetail.data}
    <div class="revision-view">
      <div class="revision-view-header">
        <button class="back-btn" onclick={() => selectedRev = null}>Back to history</button>
        <span class="rev-label">Revision {revisionDetail.data.revision_number}</span>
        <span class="rev-date">{formatDate(revisionDetail.data.created_at)}</span>
      </div>
      <div class="revision-content">
        <Markdown text={revisionDetail.data.body} streaming={false} />
      </div>
    </div>
  {:else if revisionsQuery.isLoading}
    <div class="history-loading">Loading revisions...</div>
  {:else if revisionsQuery.isError}
    <div class="history-error">
      <p>Failed to load revision history: {revisionsQuery.error?.message ?? 'Unknown error'}</p>
    </div>
  {:else if revisions.length === 0}
    <div class="history-empty">No revisions found.</div>
  {:else}
    <ul class="revision-list">
      {#each revisions as rev}
        <li>
          <button class="revision-item" onclick={() => selectedRev = rev.revision_number}>
            <div class="rev-header">
              <span class="rev-number">Rev {rev.revision_number}</span>
              <span class="rev-time">{formatDate(rev.created_at)}</span>
            </div>
            {#if rev.summary}
              <div class="rev-summary">{rev.summary}</div>
            {/if}
            {#if rev.created_by}
              <div class="rev-author">{rev.created_by}</div>
            {/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .history-panel {
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  .history-loading, .history-empty, .history-error {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    padding: 1rem 0;
    text-align: center;
  }

  .history-error {
    color: hsl(var(--status-danger));
  }

  .revision-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .revision-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.625rem 0.75rem;
    border: none;
    background: transparent;
    cursor: pointer;
    border-left: 3px solid transparent;
    transition: background 0.1s, border-color 0.1s;
    color: hsl(var(--foreground));
  }

  .revision-item:hover {
    background: hsl(var(--muted) / 0.4);
    border-left-color: hsl(var(--primary));
  }

  .rev-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .rev-number {
    font-size: 0.8125rem;
    font-weight: 500;
  }

  .rev-time {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .rev-summary {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.125rem;
  }

  .rev-author {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.125rem;
    font-style: italic;
  }

  .revision-view {
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  .revision-view-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid hsl(var(--border));
    margin-bottom: 0.75rem;
  }

  .back-btn {
    padding: 0.25rem 0.625rem;
    font-size: 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: transparent;
    color: hsl(var(--foreground));
    cursor: pointer;
  }

  .back-btn:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .rev-label {
    font-size: 0.8125rem;
    font-weight: 500;
    color: hsl(var(--foreground));
  }

  .rev-date {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .revision-content {
    flex: 1;
    overflow-y: auto;
  }
</style>
