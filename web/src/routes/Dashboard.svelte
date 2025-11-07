<script lang="ts">
  import { onMount } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import type { Theme, ActivityEntry as ActivityEntryType } from '../lib/types';
  import type { Execution } from '../lib/api';
  import { AgentMaestroAPI } from '../lib/api';
  import ScreenHeader from '../lib/components/ScreenHeader.svelte';
  import WorkflowCard from '../lib/components/WorkflowCard.svelte';
  import ActivityEntry from '../lib/components/ActivityEntry.svelte';
  import WorkflowTriggerModal from '../lib/components/WorkflowTriggerModal.svelte';
  import { workflowCards } from '../lib/stores/placeholderData';

  export let theme: Theme;

  const dispatch = createEventDispatcher<{
    navigateToTemplateGallery: void;
    navigateToWorkflowEditor: { workflowId: string };
    navigateToRunDetails: { runId: string };
  }>();

  const api = new AgentMaestroAPI();

  let viewMode: 'grid' | 'list' = 'grid';
  let searchQuery = '';
  let modalOpen = false;
  let executions: Execution[] = [];
  let loadingExecutions = false;
  let activityEntries: ActivityEntryType[] = [];

  function handleNewFromTemplate() {
    dispatch('navigateToTemplateGallery');
  }

  function handleWorkflowOpen(workflowId: string) {
    dispatch('navigateToWorkflowEditor', { workflowId });
  }

  function handleActivityViewDetails(entryId: string) {
    dispatch('navigateToRunDetails', { runId: entryId });
  }

  function toggleViewMode() {
    viewMode = viewMode === 'grid' ? 'list' : 'grid';
  }

  function extractWorkflowName(yamlString: string): string {
    try {
      const nameMatch = yamlString.match(/^\s*name:\s*["']?([^"'\n]+)["']?/m);
      return nameMatch ? nameMatch[1].trim() : 'Workflow';
    } catch {
      return 'Workflow';
    }
  }

  function formatRelativeTime(timestamp: string): string {
    try {
      const date = new Date(timestamp);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);

      if (diffMins < 1) return 'just now';
      if (diffMins < 60) return `${diffMins}m ago`;

      const diffHours = Math.floor(diffMins / 60);
      if (diffHours < 24) return `${diffHours}h ago`;

      const diffDays = Math.floor(diffHours / 24);
      return `${diffDays}d ago`;
    } catch {
      return timestamp;
    }
  }

  function calculateDuration(start: string, end?: string): string | undefined {
    if (!end) return undefined;

    try {
      const startDate = new Date(start);
      const endDate = new Date(end);
      const diffMs = endDate.getTime() - startDate.getTime();
      const diffSecs = Math.floor(diffMs / 1000);

      if (diffSecs < 60) return `${diffSecs}s`;

      const mins = Math.floor(diffSecs / 60);
      const secs = diffSecs % 60;
      return `${mins}m ${secs}s`;
    } catch {
      return undefined;
    }
  }

  function transformExecutionToActivity(execution: Execution): ActivityEntryType {
    const workflowName = extractWorkflowName(execution.workflow_id || '');
    const runNumber = parseInt(execution.id.slice(-4), 16) || 0;

    return {
      id: execution.id,
      workflowName,
      runNumber,
      status: execution.status,
      startedAt: formatRelativeTime(execution.created_at),
      version: 'v1.0.0', // TODO: Get from execution when available
      duration: calculateDuration(execution.created_at, execution.completed_at),
      progress: undefined,
      output: undefined,
      error: undefined
    };
  }

  onMount(async () => {
    loadingExecutions = true;
    try {
      executions = await api.getExecutions();
      activityEntries = executions.slice(0, 10).map(transformExecutionToActivity);
    } catch (error) {
      console.error('Failed to fetch executions:', error);
      activityEntries = [];
    } finally {
      loadingExecutions = false;
    }
  });
</script>

<div class="dashboard" class:dark={theme === 'dark'}>
  <ScreenHeader
    breadcrumbSegments={[]}
    {theme}
    on:navigate
  >
    <div slot="actions" class="header-actions">
      <button
        class="btn-new-execution"
        data-testid="new-execution-button"
        on:click={() => modalOpen = true}
      >
        New Execution
      </button>
      <button class="btn-new-template" on:click={handleNewFromTemplate}>
        New from Template
      </button>
    </div>
  </ScreenHeader>

  <WorkflowTriggerModal bind:open={modalOpen} />

  <div class="dashboard-content">
    <div class="toolbar">
      <div class="search-container">
        <input
          type="text"
          class="search-input"
          placeholder="Search workflows..."
          bind:value={searchQuery}
        />
      </div>
      <div class="view-toggle">
        <button
          class="view-btn"
          class:active={viewMode === 'grid'}
          on:click={() => viewMode = 'grid'}
          aria-label="Grid view"
        >
          ▦
        </button>
        <button
          class="view-btn"
          class:active={viewMode === 'list'}
          on:click={() => viewMode = 'list'}
          aria-label="List view"
        >
          ☰
        </button>
      </div>
    </div>

    <section class="workflows-section">
      <h2 class="section-heading">My Workflows</h2>
      <div class="workflow-grid" class:list-mode={viewMode === 'list'}>
        {#each workflowCards as workflow (workflow.id)}
          <WorkflowCard
            {workflow}
            {theme}
            on:open={() => handleWorkflowOpen(workflow.id)}
            on:run={() => console.log('Run workflow:', workflow.id)}
          />
        {/each}
      </div>
    </section>

    <section class="activity-section">
      <div class="section-header">
        <h2 class="section-heading">Recent Activity</h2>
        <button type="button" class="see-all-link" on:click={() => {}}>See All</button>
      </div>
      <div class="activity-list">
        {#if loadingExecutions}
          <div class="empty-state">Loading executions...</div>
        {:else if activityEntries.length === 0}
          <div class="empty-state">
            No recent activity. Use 'New Execution' to run workflows with inline YAML.
          </div>
        {:else}
          {#each activityEntries as entry (entry.id)}
            <ActivityEntry
              {entry}
              {theme}
              on:viewDetails={() => handleActivityViewDetails(entry.id)}
              on:viewResults={() => handleActivityViewDetails(entry.id)}
              on:debug={() => handleActivityViewDetails(entry.id)}
              on:stop={() => console.log('Stop:', entry.id)}
              on:rerun={() => console.log('Rerun:', entry.id)}
            />
          {/each}
        {/if}
      </div>
    </section>
  </div>
</div>

<style>
  .dashboard {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #f8fafc;
    overflow: auto;
  }

  .dashboard.dark {
    background: #0f172a;
  }

  .dashboard-content {
    flex: 1;
    padding: 1.5rem;
    max-width: 1400px;
    margin: 0 auto;
    width: 100%;
  }

  .header-actions {
    display: flex;
    gap: 0.75rem;
    align-items: center;
  }

  .btn-new-execution {
    padding: 0.5rem 1rem;
    background: #10b981;
    color: #ffffff;
    border: none;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }

  .btn-new-execution:hover {
    background: #059669;
  }

  .btn-new-template {
    padding: 0.5rem 1rem;
    background: #3b82f6;
    color: #ffffff;
    border: none;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }

  .btn-new-template:hover {
    background: #2563eb;
  }

  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.5rem;
    gap: 1rem;
  }

  .search-container {
    flex: 1;
    max-width: 400px;
  }

  .search-input {
    width: 100%;
    padding: 0.625rem 1rem;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    color: #0f172a;
    transition: border-color 0.2s ease;
  }

  .search-input:focus {
    outline: none;
    border-color: #3b82f6;
  }

  .dark .search-input {
    background: #1e293b;
    border-color: #334155;
    color: #e2e8f0;
  }

  .dark .search-input:focus {
    border-color: #60a5fa;
  }

  .search-input::placeholder {
    color: #94a3b8;
  }

  .view-toggle {
    display: flex;
    gap: 0.25rem;
  }

  .view-btn {
    padding: 0.5rem 0.75rem;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.25rem;
    font-size: 1rem;
    color: #64748b;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .view-btn:hover {
    background: #f1f5f9;
    border-color: #cbd5e1;
  }

  .view-btn.active {
    background: #3b82f6;
    border-color: #3b82f6;
    color: #ffffff;
  }

  .dark .view-btn {
    background: #1e293b;
    border-color: #334155;
    color: #94a3b8;
  }

  .dark .view-btn:hover {
    background: #334155;
    border-color: #475569;
  }

  .dark .view-btn.active {
    background: #3b82f6;
    border-color: #3b82f6;
    color: #ffffff;
  }

  .workflows-section {
    margin-bottom: 2rem;
  }

  .section-heading {
    margin: 0 0 1rem 0;
    font-size: 1.125rem;
    font-weight: 600;
    color: #0f172a;
  }

  .dark .section-heading {
    color: #e2e8f0;
  }

  .workflow-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1rem;
  }

  .workflow-grid.list-mode {
    grid-template-columns: 1fr;
  }

  @media (max-width: 1023px) {
    .workflow-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (max-width: 767px) {
    .workflow-grid {
      grid-template-columns: 1fr;
    }
  }

  .activity-section {
    margin-bottom: 2rem;
  }

  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1rem;
  }

  .see-all-link {
    font-size: 0.875rem;
    font-weight: 500;
    color: #3b82f6;
    text-decoration: none;
    transition: color 0.2s ease;
  }

  .see-all-link:hover {
    color: #2563eb;
    text-decoration: underline;
  }

  .dark .see-all-link {
    color: #60a5fa;
  }

  .dark .see-all-link:hover {
    color: #93c5fd;
  }

  .activity-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  @media (max-width: 480px) {
    .dashboard-content {
      padding: 1rem;
    }

    .toolbar {
      flex-direction: column;
      align-items: stretch;
    }

    .search-container {
      max-width: 100%;
    }

    .view-toggle {
      align-self: flex-end;
    }
  }
</style>
