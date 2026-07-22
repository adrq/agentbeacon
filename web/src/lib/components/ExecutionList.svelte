<script lang="ts">
  import { selectedFilterProjectId, selectedExecutionId, selectedSessionId, usageBySession } from '../stores/appState';
  import { executionsQuery, executionDetailQuery, executionAgentsQuery, executionSessionsQuery, buildSessionIdentityMap } from '../queries/executions';
  import { projectsQuery } from '../queries/projects';
  import { agentsQuery } from '../queries/agents';
  import { executionsWithQuestions } from '../stores/questionState';
  import { router } from '../router';
  import ExecutionListItem from './ExecutionListItem.svelte';
  import { useQueryClient } from '@tanstack/svelte-query';

  const queryClient = useQueryClient();
  const projects = projectsQuery();
  const execsQuery = executionsQuery(() => $selectedFilterProjectId);
  const agentsQ = agentsQuery();

  let executions = $derived(execsQuery.data ?? []);
  let questionsCount = $derived($executionsWithQuestions.size);

  let projectNameMap = $derived(
    new Map((projects.data ?? []).map(p => [p.id, p.name]))
  );

  let searchText = $state('');
  let statusFilter = $state<'all' | 'active' | 'done' | 'fail'>('all');

  // Filter by outcome presence instead of status strings
  function matchesStatusGroup(exec: import('../types').Execution, group: string): boolean {
    switch (group) {
      case 'active': return exec.outcome == null && exec.desired !== 'terminate';
      case 'done': return exec.outcome === 'completed';
      case 'fail': return exec.outcome === 'failed' || exec.outcome === 'canceled' || (exec.desired === 'terminate' && exec.outcome == null);
      default: return true;
    }
  }

  const STATUS_PILLS = [
    { value: 'all', label: 'All' },
    { value: 'active', label: 'Active' },
    { value: 'done', label: 'Done' },
    { value: 'fail', label: 'Fail' },
  ] as const;

  // Priority tier (lower sorts higher): executions needing a response first,
  // then running, then idle, then finished.
  function priorityTier(e: import('../types').Execution): number {
    if ($executionsWithQuestions.has(e.id)) return 0; // needs a response
    if (e.status === 'working') return 1;              // running
    if (e.status === 'awaiting_input') return 2;       // idle
    return 3;                                          // finished
  }

  let sorted = $derived([...executions].sort((a, b) => {
    const tierDiff = priorityTier(a) - priorityTier(b);
    if (tierDiff !== 0) return tierDiff;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  }));

  let statusFiltered = $derived(
    statusFilter === 'all'
      ? sorted
      : sorted.filter(e => matchesStatusGroup(e, statusFilter))
  );

  let filtered = $derived(
    searchText.trim()
      ? statusFiltered.filter(e => {
          const q = searchText.toLowerCase();
          return (e.title?.toLowerCase().includes(q));
        })
      : statusFiltered
  );

  // Fetch detail data for the selected execution so we can render the sidebar tree
  const selectedDetailQuery = executionDetailQuery(() => $selectedExecutionId);
  const selectedPoolQuery = executionAgentsQuery(() => $selectedExecutionId);
  const selectedSessionsDiscoveryQuery = executionSessionsQuery(() => $selectedExecutionId);
  let selectedSessionIdentity = $derived(buildSessionIdentityMap(selectedSessionsDiscoveryQuery.data ?? []));

  // Pin the selected execution if it's filtered out — keeps the sidebar tree accessible.
  // Uses the detail query (always fetched regardless of filters) so project filter changes
  // don't lose the pinned item when the execution is no longer in the executions list.
  let pinnedExecution = $derived(
    $selectedExecutionId && !filtered.some(e => e.id === $selectedExecutionId)
      ? (selectedDetailQuery.data?.execution ?? null)
      : null
  );

  let selectedSessions = $derived(selectedDetailQuery.data?.sessions ?? []);
  let selectedAgents = $derived(agentsQ.data ?? []);
  let selectedPoolAgents = $derived(selectedPoolQuery.data ?? []);
  let selectedIsTerminal = $derived(
    selectedDetailQuery.data?.execution.outcome != null
  );

  function handleAttentionClick() {
    const first = sorted.find(e => $executionsWithQuestions.has(e.id));
    if (first) router.navigate(`/execution/${first.id}`);
  }

  function handleSelectSession(sessionId: string | null) {
    selectedSessionId.set(sessionId);
  }

  function handleStatusChange(executionId: string) {
    queryClient.invalidateQueries({ queryKey: ['execution', executionId] });
    queryClient.invalidateQueries({ queryKey: ['executions'] });
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

  <div class="status-pills" role="radiogroup" aria-label="Filter by status">
    {#each STATUS_PILLS as pill}
      <button
        class="status-pill"
        class:active={statusFilter === pill.value}
        role="radio"
        aria-checked={statusFilter === pill.value}
        onclick={() => statusFilter = pill.value}
      >{pill.label}</button>
    {/each}
  </div>

  {#if questionsCount > 0}
    <button class="attention-banner" onclick={handleAttentionClick} aria-label="Jump to first execution with questions">
      <span class="attention-icon" aria-hidden="true">!</span>
      <span>{questionsCount} with questions</span>
    </button>
  {/if}

  {#if execsQuery.isLoading}
    <div class="list-message">Loading...</div>
  {:else if execsQuery.isError}
    <div class="list-message list-error">{execsQuery.error?.message ?? 'Failed to load'}</div>
  {:else}
    {#if pinnedExecution}
      <div class="pinned-item">
        <ExecutionListItem
          execution={pinnedExecution}
          projectName={projectNameMap.get(pinnedExecution.project_id ?? '') ?? null}
          sessions={selectedSessions}
          agents={selectedAgents}
          selectedSessionId={$selectedSessionId}
          usageBySession={$usageBySession}
          poolAgents={selectedPoolAgents}
          isTerminal={selectedIsTerminal}
          sessionIdentity={selectedSessionIdentity}
          onselectsession={handleSelectSession}
          onstatuschange={() => handleStatusChange(pinnedExecution!.id)}
        />
      </div>
    {/if}
    {#if filtered.length === 0}
      {#if !pinnedExecution}
        <div class="list-message">{searchText || statusFilter !== 'all' ? 'No matches' : 'No executions yet'}</div>
      {/if}
    {:else}
      {#each filtered as execution (execution.id)}
        {@const isSelected = $selectedExecutionId === execution.id}
        <ExecutionListItem
          {execution}
          projectName={projectNameMap.get(execution.project_id ?? '') ?? null}
          sessions={isSelected ? selectedSessions : undefined}
          agents={isSelected ? selectedAgents : undefined}
          selectedSessionId={isSelected ? $selectedSessionId : undefined}
          usageBySession={isSelected ? $usageBySession : undefined}
          poolAgents={isSelected ? selectedPoolAgents : undefined}
          isTerminal={isSelected ? selectedIsTerminal : false}
          sessionIdentity={isSelected ? selectedSessionIdentity : undefined}
          onselectsession={isSelected ? handleSelectSession : undefined}
          onstatuschange={isSelected ? () => handleStatusChange(execution.id) : undefined}
        />
      {/each}
    {/if}
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
    border-radius: var(--radius-sm);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.6875rem;
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
    border-radius: var(--radius-sm);
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
    border-radius: var(--radius-sm);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.6875rem;
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
    border-radius: var(--radius);
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

  .status-pills {
    display: flex;
    gap: 2px;
    padding: 0.5rem;
    border-bottom: 1px solid hsl(var(--border));
  }

  .status-pill {
    flex: 1;
    padding: 0.25rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.6875rem;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.1s, color 0.1s, border-color 0.1s;
  }

  .status-pill:hover:not(.active) {
    background: hsl(var(--muted) / 0.5);
  }

  .status-pill.active {
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
    border-color: hsl(var(--primary) / 0.3);
    font-weight: 600;
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

  .pinned-item {
    opacity: 0.65;
    border-bottom: 1px dashed hsl(var(--border));
  }
</style>
