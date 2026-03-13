<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import { projectDetailQuery, deleteProjectMutation, projectAgentsQuery, addProjectAgentMutation, removeProjectAgentMutation } from '../queries/projects';
  import { agentsQuery } from '../queries/agents';
  import { mcpServersQuery, projectMcpServersQuery, addProjectMcpServerMutation, removeProjectMcpServerMutation } from '../queries/mcp-servers';
  import type { AgentPoolEntry, McpServerPoolEntry } from '../types';
  import { executionsQuery } from '../queries/executions';
  import { router } from '../router';
  import Button from './ui/button.svelte';
  import ExecutionListItem from './ExecutionListItem.svelte';
  import { openSearchTab } from '../stores/wikiState.svelte';

  interface Props {
    projectId: string;
  }

  let { projectId }: Props = $props();

  const projectQuery = projectDetailQuery(() => projectId);
  const agents = agentsQuery();
  const projectExecsQuery = executionsQuery(() => projectId);
  const deleteMut = deleteProjectMutation();
  const poolQuery = projectAgentsQuery(() => projectId);
  const addPoolMut = addProjectAgentMutation();
  const removePoolMut = removeProjectAgentMutation();

  let project = $derived(projectQuery.data ?? null);
  let showDeleteConfirm = $state(false);
  let deleteError: string | null = $state(null);
  let showAddAgent = $state(false);
  let showAddMcpServer = $state(false);

  const allMcpServers = mcpServersQuery();
  const mcpPoolQuery = projectMcpServersQuery(() => projectId);
  const addMcpPoolMut = addProjectMcpServerMutation();
  const removeMcpPoolMut = removeProjectMcpServerMutation();

  let poolMcpServers = $derived<McpServerPoolEntry[]>(mcpPoolQuery.data ?? []);
  let poolMcpServerIds = $derived(new Set(poolMcpServers.map(s => s.mcp_server_id)));
  let availableMcpServers = $derived(
    (allMcpServers.data ?? []).filter(s => !poolMcpServerIds.has(s.id))
  );

  let poolAgents = $derived<AgentPoolEntry[]>(poolQuery.data ?? []);
  let poolAgentIds = $derived(new Set(poolAgents.map(a => a.agent_id)));
  let availableAgents = $derived(
    (agents.data ?? []).filter(a => a.enabled && !poolAgentIds.has(a.id))
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

</script>

{#if projectQuery.isLoading}
  <div class="detail-loading">Loading project...</div>
{:else if projectQuery.isError}
  <div class="detail-error">{projectQuery.error?.message ?? 'Not found'}</div>
{:else if project}
  <div class="project-detail scroll-thin">
    <div class="detail-header">
      <div class="header-top">
        <h2 class="detail-title">{project.name}</h2>
        <div class="header-actions">
          <Button variant="ghost" size="sm" onclick={() => { openSearchTab(projectId); router.navigate('#/wiki'); }}>Wiki</Button>
          <Button variant="ghost" size="sm" onclick={() => router.navigate(`/projects/${projectId}/edit`)}>Edit</Button>
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
        <span class="info-label">Created</span>
        <span class="info-value">{formatDate(project.created_at)}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Updated</span>
        <span class="info-value">{formatDate(project.updated_at)}</span>
      </div>
    </div>

    <div class="pool-section">
      <h3 class="section-heading">Agent Pool</h3>
      {#if poolAgents.length === 0}
        <p class="empty-text">No agents assigned to this project.</p>
      {:else}
        <div class="pool-tags">
          {#each poolAgents as agent (agent.agent_id)}
            <span class="pool-tag">
              {agent.name}
              <button
                class="pool-tag-remove"
                title="Remove {agent.name} from pool"
                onclick={() => removePoolMut.mutate({ projectId, agentId: agent.agent_id })}
              >&times;</button>
            </span>
          {/each}
        </div>
      {/if}
      {#if showAddAgent && availableAgents.length > 0}
        <select
          class="pool-add-select"
          onchange={(e) => {
            const agentId = e.currentTarget.value;
            if (agentId) {
              addPoolMut.mutate({ projectId, agentId });
              e.currentTarget.value = '';
              showAddAgent = false;
            }
          }}
        >
          <option value="">Select agent to add...</option>
          {#each availableAgents as agent}
            <option value={agent.id}>{agent.name}</option>
          {/each}
        </select>
      {:else if availableAgents.length > 0}
        <button class="pool-add-btn" onclick={() => showAddAgent = true}>+ Add Agent</button>
      {/if}
    </div>

    <div class="pool-section">
      <h3 class="section-heading">MCP Servers</h3>
      {#if poolMcpServers.length === 0}
        <p class="empty-text">No MCP servers assigned to this project.</p>
      {:else}
        <div class="pool-tags">
          {#each poolMcpServers as server (server.mcp_server_id)}
            <span class="pool-tag">
              {server.name}
              <button
                class="pool-tag-remove"
                title="Remove {server.name} from pool"
                onclick={() => removeMcpPoolMut.mutate({ projectId, mcpServerId: server.mcp_server_id })}
              >&times;</button>
            </span>
          {/each}
        </div>
      {/if}
      {#if showAddMcpServer && availableMcpServers.length > 0}
        <select
          class="pool-add-select"
          onchange={(e) => {
            const mcpServerId = e.currentTarget.value;
            if (mcpServerId) {
              addMcpPoolMut.mutate({ projectId, mcpServerId });
              e.currentTarget.value = '';
              showAddMcpServer = false;
            }
          }}
        >
          <option value="">Select MCP server to add...</option>
          {#each availableMcpServers as server}
            <option value={server.id}>{server.name}</option>
          {/each}
        </select>
      {:else if availableMcpServers.length > 0}
        <button class="pool-add-btn" onclick={() => showAddMcpServer = true}>+ Add MCP Server</button>
      {/if}
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
    border-radius: var(--radius-sm);
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
    border-radius: var(--radius);
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
    font-size: 0.6875rem;
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

  .pool-section {
    margin-bottom: 1.5rem;
  }

  .pool-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.375rem;
    margin-top: 0.5rem;
  }

  .pool-tag {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.125rem 0.5rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--primary) / 0.1);
    color: hsl(var(--primary));
    font-size: 0.75rem;
    font-weight: 500;
  }

  .pool-tag-remove {
    background: none;
    border: none;
    color: hsl(var(--primary) / 0.6);
    cursor: pointer;
    font-size: 0.875rem;
    line-height: 1;
    padding: 0;
  }

  .pool-tag-remove:hover {
    color: hsl(var(--status-danger));
  }

  .pool-add-btn {
    background: none;
    border: none;
    color: hsl(var(--primary));
    cursor: pointer;
    font-size: 0.75rem;
    padding: 0;
    margin-top: 0.5rem;
  }

  .pool-add-btn:hover {
    opacity: 0.8;
  }

  .pool-add-select {
    width: 100%;
    padding: 0.375rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    margin-top: 0.5rem;
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
