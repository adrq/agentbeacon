<script lang="ts">
  import { selectedFilterProjectId } from '../stores/appState';
  import { executionsQuery } from '../queries/executions';
  import { projectsQuery } from '../queries/projects';
  import { executionsWithQuestions } from '../stores/questionState';
  import { router } from '../router';
  import ExecutionListItem from './ExecutionListItem.svelte';

  const statusOrder: Record<string, number> = {
    'input-required': 0,
    'working': 1,
    'submitted': 2,
    'completed': 3,
    'failed': 4,
    'canceled': 5,
  };

  const projects = projectsQuery();
  const execsQuery = executionsQuery(() => $selectedFilterProjectId);

  let executions = $derived(execsQuery.data ?? []);

  let inputRequiredCount = $derived($executionsWithQuestions.size);

  let projectNameMap = $derived(
    new Map((projects.data ?? []).map(p => [p.id, p.name]))
  );

  let searchText = $state('');

  let sorted = $derived([...executions].sort((a, b) => {
    const orderDiff = (statusOrder[a.status] ?? 9) - (statusOrder[b.status] ?? 9);
    if (orderDiff !== 0) return orderDiff;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  }));

  let filtered = $derived(
    searchText.trim()
      ? sorted.filter(e => {
          const q = searchText.toLowerCase();
          return (e.title?.toLowerCase().includes(q)) || e.input.toLowerCase().includes(q);
        })
      : sorted
  );

  function handleAttentionClick() {
    const first = sorted.find(e => $executionsWithQuestions.has(e.id));
    if (first) router.navigate(`/execution/${first.id}`);
  }
</script>

<div class="exec-list scroll-thin">
  <div class="search-bar">
    <div class="search-input-wrapper">
      <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
      </svg>
      <input
        class="search-input"
        type="text"
        placeholder="Search executions..."
        aria-label="Search executions"
        bind:value={searchText}
      />
      {#if searchText}
        <button class="search-clear" onclick={() => searchText = ''} aria-label="Clear search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      {/if}
    </div>
  </div>

  {#if (projects.data ?? []).length > 0}
    <div class="filter-bar">
      <select
        class="filter-select"
        value={$selectedFilterProjectId ?? ''}
        onchange={(e) => selectedFilterProjectId.set(e.currentTarget.value || null)}
        aria-label="Filter by project"
      >
        <option value="">All Projects</option>
        {#each projects.data ?? [] as project}
          <option value={project.id}>{project.name}</option>
        {/each}
      </select>
    </div>
  {/if}

  {#if inputRequiredCount > 0}
    <button class="attention-banner" onclick={handleAttentionClick} aria-label="Jump to first execution awaiting input">
      <span class="attention-icon" aria-hidden="true">!</span>
      <span>{inputRequiredCount} awaiting input</span>
    </button>
  {/if}

  {#if execsQuery.isLoading}
    <div class="list-message">Loading...</div>
  {:else if execsQuery.isError}
    <div class="list-message list-error">{execsQuery.error?.message ?? 'Failed to load'}</div>
  {:else if filtered.length === 0}
    <div class="list-message">{searchText ? 'No matches' : 'No executions yet'}</div>
  {:else}
    {#each filtered as execution (execution.id)}
      <ExecutionListItem {execution} projectName={projectNameMap.get(execution.project_id ?? '') ?? null} />
    {/each}
  {/if}
</div>

<style>
  .exec-list {
    height: 100%;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .search-bar {
    padding: 0.5rem;
    border-bottom: 1px solid hsl(var(--border));
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
    padding: 0.375rem 0.5rem 0.375rem 1.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.25rem;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.75rem;
    font-family: inherit;
  }

  .search-input::placeholder {
    color: hsl(var(--muted-foreground));
  }

  .search-input:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .search-clear {
    position: absolute;
    right: 0.25rem;
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    border-radius: 0.125rem;
  }

  .search-clear:hover {
    color: hsl(var(--foreground));
  }

  .search-clear svg {
    width: 12px;
    height: 12px;
  }

  .filter-bar {
    padding: 0.5rem;
    border-bottom: 1px solid hsl(var(--border));
  }

  .filter-select {
    width: 100%;
    padding: 0.375rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.25rem;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.75rem;
    font-family: inherit;
  }

  .filter-select:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .attention-banner {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    margin: 0.5rem;
    border-radius: 0.375rem;
    background: hsl(var(--status-attention));
    color: hsl(var(--primary-foreground));
    font-size: 0.8125rem;
    font-weight: 600;
    border: none;
    cursor: pointer;
    transition: brightness 0.15s;
  }

  .attention-banner:hover {
    filter: brightness(1.1);
  }

  .attention-icon {
    width: 1.125rem;
    height: 1.125rem;
    border-radius: 50%;
    background: hsl(var(--primary-foreground));
    color: hsl(var(--status-attention));
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.6875rem;
    font-weight: 800;
    flex-shrink: 0;
  }

  .list-message {
    padding: 2rem 1rem;
    text-align: center;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .list-error {
    color: hsl(var(--status-danger));
  }
</style>
