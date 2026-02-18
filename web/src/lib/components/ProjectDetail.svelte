<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import { projectDetailQuery, deleteProjectMutation } from '../queries/projects';
  import { agentsQuery } from '../queries/agents';
  import { executionsQuery } from '../queries/executions';
  import { router } from '../router';
  import Button from './ui/button.svelte';
  import ProjectForm from './ProjectForm.svelte';
  import ExecutionListItem from './ExecutionListItem.svelte';

  interface Props {
    projectId: string;
  }

  let { projectId }: Props = $props();

  const projectQuery = projectDetailQuery(() => projectId);
  const agents = agentsQuery();
  const projectExecsQuery = executionsQuery(() => projectId);
  const deleteMut = deleteProjectMutation();

  let project = $derived(projectQuery.data ?? null);
  let editing = $state(false);
  let showDeleteConfirm = $state(false);
  let deleteError: string | null = $state(null);

  let agentNameMap = $derived(
    new Map((agents.data ?? []).map(a => [a.id, a.name]))
  );

  let executions = $derived(projectExecsQuery.data ?? []);

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  async function handleDelete() {
    deleteError = null;
    try {
      await deleteMut.mutateAsync(projectId);
      router.navigate('/projects');
    } catch (e) {
      deleteError = e instanceof Error ? e.message : 'Failed to delete';
    }
  }

  function handleEditComplete() {
    editing = false;
  }
</script>

{#if projectQuery.isLoading}
  <div class="detail-loading">Loading project...</div>
{:else if projectQuery.isError}
  <div class="detail-error">{projectQuery.error?.message ?? 'Not found'}</div>
{:else if project}
  <div class="project-detail scroll-thin">
    {#if editing}
      <ProjectForm
        {project}
        onsubmit={handleEditComplete}
        oncancel={() => editing = false}
      />
    {/if}

    <div class="detail-header">
      <div class="header-top">
        <h2 class="detail-title">{project.name}</h2>
        <div class="header-actions">
          <Button variant="ghost" size="sm" onclick={() => editing = true}>Edit</Button>
          <Button variant="ghost" size="sm" onclick={() => showDeleteConfirm = true}>Delete</Button>
        </div>
      </div>
      {#if project.is_git}
        <span class="git-badge">git</span>
      {/if}
    </div>

    <div class="info-section">
      <div class="info-row">
        <span class="info-label">Path</span>
        <span class="info-value mono">{project.path}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Default Agent</span>
        <span class="info-value">
          {project.default_agent_id ? (agentNameMap.get(project.default_agent_id) ?? 'Unknown') : 'None'}
        </span>
      </div>
      <div class="info-row">
        <span class="info-label">Created</span>
        <span class="info-value">{formatDate(project.created_at)}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Updated</span>
        <span class="info-value">{formatDate(project.updated_at)}</span>
      </div>
    </div>

    <div class="executions-section">
      <h3 class="section-heading">Recent Executions</h3>
      {#if executions.length === 0}
        <p class="empty-text">No executions for this project yet.</p>
      {:else}
        {#each executions.slice(0, 10) as execution (execution.id)}
          <ExecutionListItem {execution} />
        {/each}
      {/if}
    </div>
  </div>

  <AlertDialog.Root bind:open={showDeleteConfirm}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay class="modal-overlay" />
      <AlertDialog.Content class="modal-content">
        <AlertDialog.Title class="modal-title">Delete Project</AlertDialog.Title>
        <AlertDialog.Description class="modal-description">
          Are you sure you want to delete "{project.name}"? Existing executions will keep their project reference but you won't be able to manage this project anymore.
        </AlertDialog.Description>
        {#if deleteError}
          <div class="modal-error">{deleteError}</div>
        {/if}
        <div class="modal-actions">
          <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Cancel</AlertDialog.Cancel>
          <AlertDialog.Action class="alert-btn alert-btn-primary" onclick={handleDelete}>
            {deleteMut.isPending ? 'Deleting...' : 'Delete'}
          </AlertDialog.Action>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  </AlertDialog.Root>
{/if}

<style>
  .project-detail {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
  }

  .detail-header {
    margin-bottom: 1.5rem;
  }

  .header-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
  }

  .detail-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .header-actions {
    display: flex;
    gap: 0.25rem;
  }

  .git-badge {
    display: inline-block;
    font-size: 0.625rem;
    font-weight: 600;
    padding: 0.0625rem 0.375rem;
    border-radius: 0.25rem;
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 0.375rem;
  }

  .info-section {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 1rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.5rem;
    background: hsl(var(--card));
    margin-bottom: 1.5rem;
  }

  .info-row {
    display: flex;
    align-items: baseline;
    gap: 1rem;
  }

  .info-label {
    flex-shrink: 0;
    width: 7rem;
    font-size: 0.75rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .info-value {
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
  }

  .info-value.mono {
    font-family: var(--font-mono);
  }

  .executions-section {
    margin-top: 1rem;
  }

  .empty-text {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    padding: 1rem 0;
  }

  .detail-loading, .detail-error {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
  }

  .detail-error {
    color: hsl(var(--status-danger));
  }

  :global(.modal-description) {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    margin-bottom: 1rem;
    line-height: 1.5;
  }

  .modal-error {
    padding: 0.375rem 0.625rem;
    border-radius: 0.25rem;
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
