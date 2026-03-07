<script lang="ts">
  import { onMount } from 'svelte';
  import { AlertDialog } from 'bits-ui';
  import { ApiError } from '../../api';
  import { wikiPageQuery, putWikiPageMutation, deleteWikiPageMutation } from '../../queries/wiki';
  import { closeTab, clearTabCreateFlag, updateTabDraft, updateTabEditMeta, updateTabTitle, getWikiTabs } from '../../stores/wikiState.svelte';
  import { projectsQuery } from '../../queries/projects';
  import Markdown from '../Markdown.svelte';
  import WikiHistory from './WikiHistory.svelte';

  interface Props {
    tabId: string;
    projectId: string;
    slug: string;
  }

  let { tabId, projectId, slug }: Props = $props();

  const pageQuery = wikiPageQuery(() => projectId, () => slug);
  const putMut = putWikiPageMutation();
  const deleteMut = deleteWikiPageMutation();
  const projects = projectsQuery();

  let page = $derived(pageQuery.data ?? null);
  let tabIsCreate = $derived(getWikiTabs().find(t => t.id === tabId)?.isCreate ?? false);
  let isNewPage = $derived(tabIsCreate && !page && !pageQuery.isLoading && pageQuery.isError && pageQuery.error instanceof ApiError && pageQuery.error.status === 404);

  let projectName = $derived(
    (projects.data ?? []).find(p => p.id === projectId)?.name ?? projectId.slice(0, 8)
  );

  let editing = $state(false);
  let editView = $state<'edit' | 'preview'>('edit');
  let draftContent = $state('');
  let draftTitle = $state('');
  let editSummary = $state('');
  let showHistory = $state(false);
  let showDeleteConfirm = $state(false);
  let deleteError = $state<string | null>(null);
  let saveError = $state<string | null>(null);
  let showConflictDialog = $state(false);
  let editBaseRevision = $state<number | null>(null);

  // Restore draft from tab store on mount (run once, not reactively)
  onMount(() => {
    const tab = getWikiTabs().find(t => t.id === tabId);
    if (tab && (tab.editorBaseRevision != null || tab.editorDraft || tab.editorDraftTitle)) {
      draftContent = tab.editorDraft ?? '';
      draftTitle = tab.editorDraftTitle ?? '';
      if (tab.editorBaseRevision != null) {
        editBaseRevision = tab.editorBaseRevision;
      }
      editing = true;
    }
  });

  // Update tab title when page data loads (e.g., from deep link where slug was used as title).
  // Track last title we set to avoid redundant updates that would trigger reactive loops.
  let lastSetTitle = $state('');
  $effect(() => {
    if (page && page.title && page.title !== lastSetTitle) {
      lastSetTitle = page.title;
      updateTabTitle(tabId, page.title);
    }
  });

  // Clear stale isCreate flag when page actually exists (not a 404).
  $effect(() => {
    if (page && tabIsCreate) {
      clearTabCreateFlag(tabId);
    }
  });

  // When a draft was restored but page data arrives later, initialize edit metadata.
  // If a create-draft tab finds the page already exists (created by someone else),
  // exit edit mode and clear the stale draft to prevent silent overwrite.
  $effect(() => {
    if (page && editing && editBaseRevision === null) {
      if (tabIsCreate) {
        editing = false;
        draftContent = '';
        draftTitle = '';
        updateTabDraft(tabId, '');
        updateTabEditMeta(tabId, undefined, undefined);
        clearTabCreateFlag(tabId);
        return;
      }
      draftTitle = draftTitle || page.title;
      editBaseRevision = page.revision_number;
    }
  });

  // If page loads and it's a 404 (new page), enter edit mode
  $effect(() => {
    if (isNewPage && !editing) {
      editing = true;
      editView = 'edit';
      draftContent = '';
      draftTitle = slug;
    }
  });

  function startEditing() {
    if (!page) return;
    showHistory = false;
    draftContent = page.body;
    draftTitle = page.title;
    editSummary = '';
    editBaseRevision = page.revision_number;
    editing = true;
    editView = 'edit';
    saveError = null;
    updateTabDraft(tabId, page.body);
    updateTabEditMeta(tabId, page.revision_number, page.title);
  }

  function cancelEditing() {
    if (isNewPage) {
      closeTab(tabId);
      return;
    }
    editing = false;
    draftContent = '';
    editSummary = '';
    saveError = null;
    updateTabDraft(tabId, '');
    updateTabEditMeta(tabId, undefined, undefined);
  }

  function handleDraftChange(value: string) {
    draftContent = value;
    updateTabDraft(tabId, value);
  }

  async function handleSave() {
    saveError = null;
    const req = {
      title: draftTitle || slug,
      body: draftContent,
      summary: editSummary || undefined,
      revision_number: isNewPage ? undefined : (editBaseRevision ?? undefined),
    };

    try {
      await putMut.mutateAsync({ projectId, slug, req });
      editing = false;
      draftContent = '';
      editSummary = '';
      updateTabDraft(tabId, '');
      updateTabEditMeta(tabId, undefined, undefined);
      clearTabCreateFlag(tabId);
      updateTabTitle(tabId, req.title);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        let errorType = '';
        try { errorType = JSON.parse(e.body).error; } catch { /* fallback */ }
        if (errorType === 'slug_exists') {
          saveError = 'A page with this slug already exists.';
        } else {
          showConflictDialog = true;
        }
      } else {
        saveError = e instanceof Error ? e.message : 'Failed to save';
      }
    }
  }

  async function handleConflictReload() {
    showConflictDialog = false;
    await pageQuery.refetch();
    if (pageQuery.data) {
      draftContent = pageQuery.data.body;
      draftTitle = pageQuery.data.title;
      editBaseRevision = pageQuery.data.revision_number;
      updateTabDraft(tabId, pageQuery.data.body);
      updateTabEditMeta(tabId, pageQuery.data.revision_number, pageQuery.data.title);
    }
  }

  async function handleConflictKeepEditing() {
    showConflictDialog = false;
    await pageQuery.refetch();
    if (pageQuery.data) {
      editBaseRevision = pageQuery.data.revision_number;
      updateTabEditMeta(tabId, pageQuery.data.revision_number, draftTitle);
    }
  }

  async function handleDelete() {
    deleteError = null;
    try {
      await deleteMut.mutateAsync({ projectId, slug });
      closeTab(tabId);
    } catch (e) {
      deleteError = e instanceof Error ? e.message : 'Failed to delete';
    }
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }
</script>

<div class="page-view scroll-thin">
  {#if pageQuery.isLoading}
    <div class="page-loading">Loading page...</div>
  {:else if isNewPage && editing}
    <!-- New page creation mode -->
    <div class="page-header">
      <div class="header-top">
        <h2 class="page-title">New Page: {slug}</h2>
      </div>
      <div class="page-meta">
        <span>{projectName}</span>
      </div>
    </div>

    <div class="editor-area">
      <div class="editor-header">
        <div class="view-toggle" role="tablist" aria-label="Editor mode">
          <button class="toggle-btn" class:active={editView === 'edit'} role="tab" aria-selected={editView === 'edit'} onclick={() => editView = 'edit'}>Edit</button>
          <button class="toggle-btn" class:active={editView === 'preview'} role="tab" aria-selected={editView === 'preview'} onclick={() => editView = 'preview'}>Preview</button>
        </div>
      </div>

      <label class="title-label">
        Title
        <input class="title-input" type="text" bind:value={draftTitle} placeholder={slug} oninput={(e) => updateTabEditMeta(tabId, editBaseRevision ?? undefined, e.currentTarget.value)} />
      </label>

      {#if editView === 'edit'}
        <textarea
          class="editor-textarea"
          value={draftContent}
          oninput={(e) => handleDraftChange(e.currentTarget.value)}
          placeholder="Write your page content in Markdown..."
        ></textarea>
      {:else}
        <div class="preview-area">
          <Markdown text={draftContent} streaming={false} />
        </div>
      {/if}

      <div class="editor-footer">
        <input class="summary-input" type="text" placeholder="Edit summary (optional)" aria-label="Edit summary" bind:value={editSummary} />
        <div class="editor-actions">
          <button class="cancel-btn" onclick={cancelEditing}>Cancel</button>
          <button class="save-btn" disabled={putMut.isPending} onclick={handleSave}>
            {putMut.isPending ? 'Creating...' : 'Create'}
          </button>
        </div>
      </div>
      {#if saveError}
        <div class="save-error" role="alert">{saveError}</div>
      {/if}
    </div>
  {:else if pageQuery.isError && !isNewPage}
    <div class="page-error">
      {#if pageQuery.error instanceof ApiError && pageQuery.error.status === 404}
        <p>Page not found.</p>
      {:else}
        <p>Failed to load page: {pageQuery.error?.message ?? 'Unknown error'}</p>
        <button class="close-tab-btn" onclick={() => pageQuery.refetch()}>Retry</button>
      {/if}
      <button class="close-tab-btn" onclick={() => closeTab(tabId)}>Close tab</button>
    </div>
  {:else if page}
    <div class="page-header">
      <div class="header-top">
        <h2 class="page-title">{page.title}</h2>
        <div class="header-actions">
          {#if !editing}
            <button class="action-btn" onclick={startEditing}>Edit</button>
            <button class="action-btn" onclick={() => showHistory = !showHistory}>
              {showHistory ? 'Content' : 'History'}
            </button>
            <button class="action-btn action-btn-danger" onclick={() => showDeleteConfirm = true}>Delete</button>
          {/if}
        </div>
      </div>
      <div class="page-meta">
        <span>{projectName}</span>
        <span class="meta-sep">&middot;</span>
        <span>rev {page.revision_number}</span>
        <span class="meta-sep">&middot;</span>
        <span>{formatDate(page.updated_at)}</span>
        {#if page.updated_by}
          <span class="meta-sep">&middot;</span>
          <span>{page.updated_by}</span>
        {/if}
      </div>
    </div>

    {#if showHistory}
      <WikiHistory {projectId} {slug} />
    {:else if editing}
      <div class="editor-area">
        <div class="editor-header">
          <div class="view-toggle" role="tablist" aria-label="Editor mode">
            <button class="toggle-btn" class:active={editView === 'edit'} role="tab" aria-selected={editView === 'edit'} onclick={() => editView = 'edit'}>Edit</button>
            <button class="toggle-btn" class:active={editView === 'preview'} role="tab" aria-selected={editView === 'preview'} onclick={() => editView = 'preview'}>Preview</button>
          </div>
        </div>

        <label class="title-label">
          Title
          <input class="title-input" type="text" bind:value={draftTitle} oninput={(e) => updateTabEditMeta(tabId, editBaseRevision ?? undefined, e.currentTarget.value)} />
        </label>

        {#if editView === 'edit'}
          <textarea
            class="editor-textarea"
            value={draftContent}
            oninput={(e) => handleDraftChange(e.currentTarget.value)}
          ></textarea>
        {:else}
          <div class="preview-area">
            <Markdown text={draftContent} streaming={false} />
          </div>
        {/if}

        <div class="editor-footer">
          <input class="summary-input" type="text" placeholder="Edit summary (optional)" aria-label="Edit summary" bind:value={editSummary} />
          <div class="editor-actions">
            <button class="cancel-btn" onclick={cancelEditing}>Cancel</button>
            <button class="save-btn" disabled={putMut.isPending} onclick={handleSave}>
              {putMut.isPending ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
        {#if saveError}
          <div class="save-error" role="alert">{saveError}</div>
        {/if}
      </div>
    {:else}
      <div class="page-body">
        <Markdown text={page.body} streaming={false} />
      </div>
    {/if}
  {/if}
</div>

<!-- Delete confirmation -->
<AlertDialog.Root bind:open={showDeleteConfirm}>
  <AlertDialog.Portal>
    <AlertDialog.Overlay class="modal-overlay" />
    <AlertDialog.Content class="modal-content">
      <AlertDialog.Title class="modal-title">Delete Wiki Page</AlertDialog.Title>
      <AlertDialog.Description class="modal-description">
        Are you sure you want to delete "{slug}"? This action cannot be undone.
      </AlertDialog.Description>
      {#if deleteError}
        <div class="modal-error" role="alert">{deleteError}</div>
      {/if}
      <div class="modal-actions">
        <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Cancel</AlertDialog.Cancel>
        <AlertDialog.Action class="alert-btn alert-btn-danger" onclick={handleDelete}>
          {deleteMut.isPending ? 'Deleting...' : 'Delete'}
        </AlertDialog.Action>
      </div>
    </AlertDialog.Content>
  </AlertDialog.Portal>
</AlertDialog.Root>

<!-- OCC Conflict dialog -->
<AlertDialog.Root bind:open={showConflictDialog}>
  <AlertDialog.Portal>
    <AlertDialog.Overlay class="modal-overlay" />
    <AlertDialog.Content class="modal-content">
      <AlertDialog.Title class="modal-title">Edit Conflict</AlertDialog.Title>
      <AlertDialog.Description class="modal-description">
        This page has been modified by someone else since you started editing. You can reload the latest version (discarding your edits) or keep your changes and overwrite the latest version on next save.
      </AlertDialog.Description>
      <div class="modal-actions">
        <AlertDialog.Cancel class="alert-btn alert-btn-ghost" onclick={handleConflictKeepEditing}>Keep my changes</AlertDialog.Cancel>
        <AlertDialog.Action class="alert-btn alert-btn-primary" onclick={handleConflictReload}>Reload latest</AlertDialog.Action>
      </div>
    </AlertDialog.Content>
  </AlertDialog.Portal>
</AlertDialog.Root>

<style>
  .page-view {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    display: flex;
    flex-direction: column;
  }

  .page-loading, .page-error {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
    gap: 0.75rem;
  }

  .close-tab-btn {
    padding: 0.375rem 0.75rem;
    font-size: 0.8125rem;
    background: hsl(var(--muted));
    color: hsl(var(--foreground));
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
  }

  .page-header {
    margin-bottom: 1rem;
  }

  .header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .page-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .header-actions {
    display: flex;
    gap: 0.25rem;
  }

  .action-btn {
    padding: 0.25rem 0.625rem;
    font-size: 0.6875rem;
    font-weight: 500;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    border-radius: var(--radius);
    transition: color 0.15s, background 0.15s;
  }

  .action-btn:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .action-btn-danger:hover {
    color: hsl(var(--status-danger));
    background: hsl(var(--status-danger) / 0.1);
  }

  .page-meta {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
    display: flex;
    gap: 0.25rem;
    align-items: center;
  }

  .meta-sep {
    opacity: 0.5;
  }

  .page-body {
    flex: 1;
    padding: 0.5rem 0;
  }

  /* Editor */
  .editor-area {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .editor-header {
    display: flex;
    align-items: center;
  }

  .view-toggle {
    display: flex;
    gap: 0;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    overflow: hidden;
  }

  .toggle-btn {
    padding: 0.25rem 0.75rem;
    font-size: 0.75rem;
    font-weight: 500;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: background 0.15s, color 0.15s;
  }

  .toggle-btn.active {
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
  }

  .toggle-btn:not(.active):hover {
    background: hsl(var(--muted) / 0.3);
  }

  .title-label {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .title-input {
    padding: 0.375rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    color: hsl(var(--foreground));
    font-size: 0.875rem;
    font-weight: 500;
    outline: none;
  }

  .title-input:focus {
    border-color: hsl(var(--primary));
  }

  .editor-textarea {
    flex: 1;
    min-height: 300px;
    padding: 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    color: hsl(var(--foreground));
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    line-height: 1.6;
    resize: vertical;
    outline: none;
  }

  .editor-textarea:focus {
    border-color: hsl(var(--primary));
  }

  .preview-area {
    flex: 1;
    min-height: 300px;
    padding: 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
  }

  .editor-footer {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }

  .summary-input {
    flex: 1;
    padding: 0.375rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    outline: none;
  }

  .summary-input:focus {
    border-color: hsl(var(--primary));
  }

  .editor-actions {
    display: flex;
    gap: 0.375rem;
  }

  .save-btn {
    padding: 0.375rem 0.75rem;
    font-size: 0.8125rem;
    font-weight: 500;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
  }

  .save-btn:hover {
    filter: brightness(1.1);
  }

  .save-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .cancel-btn {
    padding: 0.375rem 0.75rem;
    font-size: 0.8125rem;
    background: transparent;
    color: hsl(var(--foreground));
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
  }

  .cancel-btn:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .save-error {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
  }

  .modal-error {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
    margin-bottom: 1rem;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
