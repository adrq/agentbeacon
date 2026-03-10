<script lang="ts">
  import './diff-theme.css';
  import DiffSummaryBar from './DiffSummaryBar.svelte';
  import DiffFileList from './DiffFileList.svelte';
  import { sessionDiffQuery } from '../queries/executions';

  interface Props {
    sessionId: string | null;
    isTerminal: boolean;
  }
  let { sessionId, isTerminal }: Props = $props();

  // Lazy-load diff2html UI (includes highlight.js)
  let Diff2HtmlUI: typeof import('diff2html/lib/ui/js/diff2html-ui').Diff2HtmlUI | null = $state(null);
  let loadError = $state(false);

  function loadDiff2Html() {
    loadError = false;
    import('diff2html/lib/ui/js/diff2html-ui')
      .then(m => { Diff2HtmlUI = m.Diff2HtmlUI; })
      .catch(() => { loadError = true; });
  }

  $effect(() => { if (!Diff2HtmlUI) loadDiff2Html(); });

  const diffQuery = sessionDiffQuery(() => sessionId, () => isTerminal);

  let diffData = $derived(diffQuery.data ?? null);
  let loading = $derived(diffQuery.isLoading);
  let errorMsg = $derived(diffQuery.error?.message ?? '');

  // Match specific backend messages — other 404/400 errors (e.g. session not found,
  // invalid base ref, git diff failures) fall through to the generic error state.
  let isNoWorktree = $derived(
    errorMsg.includes('no worktree or working directory') ||
    errorMsg.includes('worktree directory no longer exists')
  );
  let isNotGit = $derived(errorMsg.includes('not a git repository'));
  let hasError = $derived(diffQuery.isError && !isNoWorktree && !isNotGit);
  let noChanges = $derived(diffData !== null && diffData.files.length === 0);
  let truncated = $derived(diffData?.truncated === true);

  // DOM ref for diff2html rendering
  let diffContainer: HTMLDivElement | undefined = $state();

  $effect(() => {
    if (!Diff2HtmlUI || !diffData?.patch || !diffContainer) return;
    const ui = new Diff2HtmlUI(diffContainer, diffData.patch, {
      drawFileList: false,
      outputFormat: 'line-by-line',
      matching: 'lines',
      highlight: true,
      stickyFileHeaders: false,
      fileListToggle: false,
      fileContentToggle: false,
    });
    ui.draw();
    return () => { if (diffContainer) diffContainer.innerHTML = ''; };
  });
</script>

<div class="diff-panel scroll-thin">
  {#if loadError}
    <div class="diff-empty diff-error">
      Failed to load diff viewer
      <button class="retry-btn" onclick={loadDiff2Html}>Retry</button>
    </div>
  {:else if loading && !diffData}
    <div class="diff-empty">Loading diff...</div>
  {:else if isNoWorktree}
    <div class="diff-empty">No worktree for this session</div>
  {:else if isNotGit}
    <div class="diff-empty">Not a git repository</div>
  {:else if hasError}
    <div class="diff-empty diff-error">Failed to load diff</div>
  {:else if noChanges}
    <div class="diff-empty">No changes detected</div>
  {:else if truncated && diffData}
    <div class="diff-truncated">
      <DiffSummaryBar data={diffData} />
      <p class="truncated-msg">
        Diff too large to display ({diffData.summary.files_changed} file{diffData.summary.files_changed !== 1 ? 's' : ''},
        +{diffData.summary.insertions} -{diffData.summary.deletions})
      </p>
    </div>
  {:else if diffData && diffData.files.length > 0}
    <DiffSummaryBar data={diffData} />
    <DiffFileList files={diffData.files} />
    <div class="diff-content" bind:this={diffContainer}></div>
  {/if}
</div>

<style>
  .diff-panel {
    flex: 1;
    min-height: 0;
    min-width: 0;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 0.75rem 1rem;
  }

  .diff-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    min-height: 8rem;
    font-size: var(--text-sm);
    color: hsl(var(--muted-foreground));
  }

  .diff-error {
    color: hsl(var(--status-danger));
  }

  .retry-btn {
    padding: 0.25rem 0.75rem;
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: transparent;
    color: hsl(var(--foreground));
    font-size: var(--text-sm);
    cursor: pointer;
    transition: background 0.1s;
  }

  .retry-btn:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .diff-truncated {
    padding: 0.5rem 0;
  }

  .truncated-msg {
    font-size: var(--text-sm);
    color: hsl(var(--status-attention));
    margin-top: 0.375rem;
  }

  .diff-content {
    overflow-x: auto;
    min-width: 0;
    max-width: 100%;
  }

  /* Prevent diff2html tables from pushing the container wider than viewport.
     Each file-diff scrolls independently within its wrapper. */
  .diff-content :global(.d2h-wrapper) {
    max-width: 100%;
    overflow: hidden;
  }

  .diff-content :global(.d2h-file-wrapper) {
    max-width: 100%;
  }

  .diff-content :global(.d2h-file-diff) {
    overflow-x: auto;
  }
</style>
