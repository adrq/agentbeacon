<script lang="ts">
  import type { DiffFileEntry } from '../types';

  interface Props {
    files: DiffFileEntry[];
  }
  let { files }: Props = $props();

  function scrollToFile(filePath: string) {
    const nameEls = document.querySelectorAll('.diff-panel .d2h-file-name');
    for (const el of nameEls) {
      if (el.textContent?.trim() === filePath) {
        el.closest('.d2h-file-wrapper')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
      }
    }
  }
</script>

<div class="diff-file-list">
  {#each files as file}
    <button class="diff-file-entry" onclick={() => scrollToFile(file.path)}>
      <span
        class="file-status"
        class:added={file.status === 'A'}
        class:deleted={file.status === 'D'}
        class:modified={file.status === 'M'}
      >
        {file.status}
      </span>
      <span class="file-path">{file.path}</span>
      <span class="file-stats">
        <span class="stat-add">+{file.insertions}</span>
        <span class="stat-del">-{file.deletions}</span>
      </span>
    </button>
  {/each}
</div>

<style>
  .diff-file-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
    margin-bottom: 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    overflow: hidden;
  }

  .diff-file-entry {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.25rem 0.75rem;
    background: hsl(var(--muted) / 0.1);
    border: none;
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    color: hsl(var(--foreground));
    text-align: left;
    transition: background 0.1s;
  }

  .diff-file-entry:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .file-status {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.25rem;
    height: 1.25rem;
    border-radius: var(--radius-sm);
    font-size: var(--text-xs);
    font-weight: 600;
    flex-shrink: 0;
  }

  .file-status.added {
    color: hsl(var(--status-success));
    background: hsl(var(--status-success) / 0.15);
  }

  .file-status.deleted {
    color: hsl(var(--status-danger));
    background: hsl(var(--status-danger) / 0.15);
  }

  .file-status.modified {
    color: hsl(var(--status-attention));
    background: hsl(var(--status-attention) / 0.15);
  }

  .file-path {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .file-stats {
    display: flex;
    gap: 0.375rem;
    font-size: var(--text-xs);
    font-weight: 500;
    flex-shrink: 0;
  }

  .stat-add {
    color: hsl(var(--status-success));
  }

  .stat-del {
    color: hsl(var(--status-danger));
  }
</style>
