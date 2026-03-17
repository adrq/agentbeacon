<script lang="ts">
  import type { DiffCommitEntry } from '../types';

  interface Props {
    commits: DiffCommitEntry[];
    onSelectBase?: (sha: string) => void;
  }
  let { commits, onSelectBase }: Props = $props();
</script>

<div class="diff-commit-list">
  <span class="commit-list-label">{commits.length} commit{commits.length !== 1 ? 's' : ''}</span>
  {#each commits as commit}
    <div class="commit-entry">
      <button
        class="commit-sha"
        title="Diff from this commit"
        onclick={() => onSelectBase?.(commit.sha)}
      >{commit.sha.slice(0, 7)}</button>
      <span class="commit-message">{commit.message}</span>
      <span class="commit-author">{commit.author}</span>
    </div>
  {/each}
</div>

<style>
  .diff-commit-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
    margin-bottom: 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    overflow: hidden;
  }

  .commit-list-label {
    padding: 0.25rem 0.75rem;
    font-size: var(--text-xs);
    font-weight: 600;
    color: hsl(var(--muted-foreground));
    background: hsl(var(--muted) / 0.2);
  }

  .commit-entry {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.25rem 0.75rem;
    background: hsl(var(--muted) / 0.1);
    font-size: var(--text-sm);
    color: hsl(var(--foreground));
  }

  .commit-sha {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: hsl(var(--primary));
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    text-decoration: none;
  }

  .commit-sha:hover {
    text-decoration: underline;
  }

  .commit-message {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .commit-author {
    flex-shrink: 0;
    font-size: var(--text-xs);
    color: hsl(var(--muted-foreground));
  }
</style>
