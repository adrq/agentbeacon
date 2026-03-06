<script lang="ts">
  import type { DiffResponse } from '../types';

  interface Props {
    data: DiffResponse;
  }
  let { data }: Props = $props();

  let fileLabel = $derived(
    data.summary.files_changed === 1 ? '1 file changed' : `${data.summary.files_changed} files changed`
  );
</script>

<div class="diff-summary-bar">
  <span class="diff-stat-files">{fileLabel}</span>
  <span class="diff-stat-add">+{data.summary.insertions}</span>
  <span class="diff-stat-del">-{data.summary.deletions}</span>
</div>

<style>
  .diff-summary-bar {
    display: flex;
    align-items: center;
    gap: 0.625rem;
    padding: 0.375rem 0.75rem;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    background: hsl(var(--muted) / 0.2);
    border-radius: var(--radius);
    margin-bottom: 0.5rem;
  }

  .diff-stat-add {
    color: hsl(var(--status-success));
  }

  .diff-stat-del {
    color: hsl(var(--status-danger));
  }
</style>
