<script lang="ts">
  import { projectsQuery } from '../../queries/projects';
  import { wikiPagesQuery } from '../../queries/wiki';
  import { openPage, updateTabProjectId } from '../../stores/wikiState.svelte';

  interface Props {
    tabId: string;
    initialProjectId?: string;
  }

  let { tabId, initialProjectId }: Props = $props();

  let selectedProjectId = $state<string | null>(initialProjectId ?? null);
  let searchText = $state('');
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let debouncedQuery = $state('');

  const projects = projectsQuery();

  // Auto-select first project if none selected or selected project no longer exists
  $effect(() => {
    const projectList = projects.data ?? [];
    if (projectList.length === 0) return;
    if (!selectedProjectId) {
      selectedProjectId = projectList[0].id;
      updateTabProjectId(tabId, selectedProjectId);
    } else if (!projectList.some(p => p.id === selectedProjectId)) {
      selectedProjectId = null;
      updateTabProjectId(tabId, null);
    }
  });

  // Debounce search input
  $effect(() => {
    const text = searchText;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { debouncedQuery = text; }, 250);
    return () => { if (debounceTimer) clearTimeout(debounceTimer); };
  });

  const pagesQuery = wikiPagesQuery(
    () => selectedProjectId,
    () => debouncedQuery || undefined,
  );

  let pages = $derived(pagesQuery.data ?? []);

  function selectedProjectName(): string | undefined {
    return (projects.data ?? []).find(p => p.id === selectedProjectId)?.name;
  }

  function handleResultClick(slug: string, title: string) {
    if (!selectedProjectId) return;
    openPage(selectedProjectId, slug, title, undefined, selectedProjectName());
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  }

  let showCreateForm = $state(false);
  let newSlug = $state('');
  let slugError = $state('');

  function validateSlug(s: string): boolean {
    return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(s);
  }

  function handleCreateClick() {
    showCreateForm = true;
    newSlug = searchText.toLowerCase().replace(/\s+/g, '-').replace(/[^a-z0-9-]/g, '');
  }

  function handleCreateOpen() {
    if (!selectedProjectId) return;
    if (!newSlug) { slugError = 'Slug is required'; return; }
    if (!validateSlug(newSlug)) { slugError = 'Lowercase letters, numbers, hyphens only'; return; }
    slugError = '';
    showCreateForm = false;
    // Open as a new page tab with create intent
    openPage(selectedProjectId, newSlug, newSlug, true, selectedProjectName());
  }
</script>

{#snippet createForm()}
  {#if showCreateForm}
    <div class="create-form">
      <label class="create-label">
        Page slug
        <input
          class="create-input"
          type="text"
          placeholder="my-page-slug"
          bind:value={newSlug}
          aria-label="New page slug"
        />
      </label>
      {#if slugError}
        <div class="slug-error">{slugError}</div>
      {/if}
      <div class="create-actions">
        <button class="create-btn" onclick={handleCreateOpen}>Create</button>
        <button class="cancel-btn" onclick={() => showCreateForm = false}>Cancel</button>
      </div>
    </div>
  {:else}
    <button class="create-page-link" onclick={handleCreateClick}>Create new page</button>
  {/if}
{/snippet}

<div class="search-tab scroll-thin">
  <div class="search-controls">
    <select
      class="project-select"
      value={selectedProjectId ?? ''}
      onchange={(e) => { selectedProjectId = e.currentTarget.value || null; updateTabProjectId(tabId, selectedProjectId); }}
      aria-label="Select project"
    >
      <option value="" disabled>Select a project...</option>
      {#each projects.data ?? [] as project}
        <option value={project.id}>{project.name}</option>
      {/each}
    </select>

    <div class="search-input-wrapper">
      <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <input
        class="search-input"
        type="text"
        placeholder="Search wiki pages..."
        aria-label="Search wiki pages"
        bind:value={searchText}
      />
      {#if searchText}
        <button class="search-clear" onclick={() => searchText = ''} aria-label="Clear search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      {/if}
    </div>
  </div>

  <div class="results-area">
    {#if !selectedProjectId}
      <div class="empty-state">Select a project to browse wiki pages.</div>
    {:else if pagesQuery.isLoading}
      <div class="empty-state">Loading...</div>
    {:else if pagesQuery.isError}
      <div class="empty-state">Failed to load pages: {pagesQuery.error?.message ?? 'Unknown error'}</div>
    {:else if pages.length === 0}
      <div class="empty-state">
        {#if searchText}
          No pages matching "{searchText}".
        {:else}
          No wiki pages yet.
        {/if}
      </div>
      {@render createForm()}
    {:else}
      <ul class="results-list">
        {#each pages as page}
          <li>
            <button class="result-item" onclick={() => handleResultClick(page.slug, page.title)}>
              <div class="result-title">{page.title}</div>
              <div class="result-meta">
                <span>{page.slug}</span>
                <span class="meta-sep">&middot;</span>
                <span>rev {page.revision_number}</span>
                <span class="meta-sep">&middot;</span>
                <span>{formatDate(page.updated_at)}</span>
              </div>
            </button>
          </li>
        {/each}
      </ul>
      {@render createForm()}
    {/if}
  </div>
</div>

<style>
  .search-tab {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .search-controls {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .project-select {
    width: 100%;
    padding: 0.375rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    outline: none;
  }

  .project-select:focus {
    border-color: hsl(var(--primary));
  }

  .search-input-wrapper {
    position: relative;
    display: flex;
    align-items: center;
  }

  .search-icon {
    position: absolute;
    left: 0.5rem;
    width: 14px;
    height: 14px;
    color: hsl(var(--muted-foreground));
    pointer-events: none;
  }

  .search-input {
    width: 100%;
    padding: 0.375rem 0.625rem 0.375rem 1.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    outline: none;
  }

  .search-input:focus {
    border-color: hsl(var(--primary));
  }

  .search-input::placeholder {
    color: hsl(var(--muted-foreground));
  }

  .search-clear {
    position: absolute;
    right: 0.375rem;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    border-radius: var(--radius-sm);
  }

  .search-clear:hover {
    color: hsl(var(--foreground));
  }

  .search-clear svg {
    width: 12px;
    height: 12px;
  }

  .results-area {
    flex: 1;
    display: flex;
    flex-direction: column;
  }

  .empty-state {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    padding: 1rem 0;
    text-align: center;
  }

  .results-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .result-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.625rem 0.75rem;
    border: none;
    background: transparent;
    border-left: 3px solid transparent;
    cursor: pointer;
    transition: background 0.1s, border-color 0.1s;
    color: hsl(var(--foreground));
  }

  .result-item:hover {
    background: hsl(var(--muted) / 0.4);
    border-left-color: hsl(var(--primary));
  }

  .result-title {
    font-size: 0.8125rem;
    font-weight: 500;
  }

  .result-meta {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.125rem;
    display: flex;
    gap: 0.25rem;
    align-items: center;
  }

  .meta-sep {
    opacity: 0.5;
  }

  .create-page-link {
    display: inline-block;
    margin-top: 0.5rem;
    padding: 0.375rem 0.75rem;
    font-size: 0.8125rem;
    color: hsl(var(--primary));
    background: transparent;
    border: 1px solid hsl(var(--primary) / 0.3);
    border-radius: var(--radius);
    cursor: pointer;
    align-self: flex-start;
  }

  .create-page-link:hover {
    background: hsl(var(--primary) / 0.08);
  }

  .create-form {
    margin-top: 0.5rem;
    padding: 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .create-label {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .create-input {
    padding: 0.375rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: var(--font-mono);
    outline: none;
  }

  .create-input:focus {
    border-color: hsl(var(--primary));
  }

  .slug-error {
    font-size: 0.75rem;
    color: hsl(var(--status-danger));
  }

  .create-actions {
    display: flex;
    gap: 0.5rem;
  }

  .create-btn {
    padding: 0.25rem 0.625rem;
    font-size: 0.8125rem;
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    border: none;
    border-radius: var(--radius);
    cursor: pointer;
  }

  .create-btn:hover {
    filter: brightness(1.1);
  }

  .cancel-btn {
    padding: 0.25rem 0.625rem;
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
</style>
