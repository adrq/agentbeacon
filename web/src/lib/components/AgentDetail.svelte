<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import { agentDetailQuery, deleteAgentMutation, driversQuery } from '../queries/agents';
  import { executionsQuery } from '../queries/executions';
  import { router } from '../router';
  import { typeLabels } from '../utils/agentUtils';
  import Button from './ui/button.svelte';
  import AgentForm from './AgentForm.svelte';
  import ExecutionListItem from './ExecutionListItem.svelte';

  interface Props {
    agentId: string;
  }

  let { agentId }: Props = $props();

  const agentQuery = agentDetailQuery(() => agentId);
  const drivers = driversQuery();
  const allExecsQuery = executionsQuery();
  const deleteMut = deleteAgentMutation();

  let agent = $derived(agentQuery.data ?? null);
  let editing = $state(false);
  let showDeleteConfirm = $state(false);
  let deleteError: string | null = $state(null);

  let driverNameMap = $derived(
    new Map((drivers.data ?? []).map(d => [d.id, d.name]))
  );

  let executions = $derived(
    (allExecsQuery.data ?? []).filter(e => {
      const meta = e.metadata as Record<string, unknown>;
      return meta?.agent_id === agentId;
    })
  );

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  function maskApiKey(key: unknown): string {
    if (typeof key !== 'string') return String(key);
    if (key.length <= 8) return key;
    return '\u25CF\u25CF\u25CF\u25CF\u25CF\u25CF' + key.slice(-4);
  }

  async function handleDelete() {
    deleteError = null;
    try {
      await deleteMut.mutateAsync(agentId);
      router.navigate('/agents');
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to delete';
      if (msg.includes('409')) {
        deleteError = 'Cannot delete agent with active sessions.';
      } else {
        deleteError = msg;
      }
    }
  }

  function handleEditComplete() {
    editing = false;
  }
</script>

{#if agentQuery.isLoading}
  <div class="detail-loading">Loading agent...</div>
{:else if agentQuery.isError}
  <div class="detail-error">{agentQuery.error?.message ?? 'Not found'}</div>
{:else if agent}
  <div class="agent-detail scroll-thin">
    {#if editing}
      <AgentForm
        {agent}
        onsubmit={handleEditComplete}
        oncancel={() => editing = false}
      />
    {/if}

    <div class="detail-header">
      <div class="header-top">
        <h2 class="detail-title">{agent.name}</h2>
        <div class="header-actions">
          <Button variant="ghost" size="sm" onclick={() => editing = true}>Edit</Button>
          <Button variant="ghost" size="sm" onclick={() => { deleteError = null; showDeleteConfirm = true; }}>Delete</Button>
        </div>
      </div>
      <div class="header-badges">
        <span class="type-badge">{typeLabels[agent.agent_type] ?? agent.agent_type}</span>
        {#if !agent.enabled}
          <span class="disabled-badge">disabled</span>
        {/if}
      </div>
    </div>

    <div class="info-section">
      <div class="info-row">
        <span class="info-label">Type</span>
        <span class="info-value">{typeLabels[agent.agent_type] ?? agent.agent_type}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Driver</span>
        <span class="info-value">{agent.driver_id ? (driverNameMap.get(agent.driver_id) ?? agent.driver_id) : 'None'}</span>
      </div>
      {#if agent.description}
        <div class="info-row">
          <span class="info-label">Description</span>
          <span class="info-value">{agent.description}</span>
        </div>
      {/if}
      {#each Object.entries(agent.config) as [key, value]}
        <div class="info-row">
          <span class="info-label">{key}</span>
          <span class="info-value mono">
            {#if key.toLowerCase().includes('key') || key.toLowerCase().includes('token') || key.toLowerCase().includes('secret')}
              {maskApiKey(value)}
            {:else if typeof value === 'object'}
              {JSON.stringify(value)}
            {:else}
              {String(value)}
            {/if}
          </span>
        </div>
      {/each}
      <div class="info-row">
        <span class="info-label">Created</span>
        <span class="info-value">{formatDate(agent.created_at)}</span>
      </div>
      <div class="info-row">
        <span class="info-label">Updated</span>
        <span class="info-value">{formatDate(agent.updated_at)}</span>
      </div>
    </div>

    <div class="executions-section">
      <h3 class="section-heading">Recent Executions</h3>
      {#if executions.length === 0}
        <p class="empty-text">No executions for this agent yet.</p>
      {:else}
        {#each executions.slice(0, 10) as execution (execution.id)}
          <ExecutionListItem {execution} />
        {/each}
      {/if}
    </div>
  </div>

  <AlertDialog.Root open={showDeleteConfirm} onOpenChange={(o) => { if (!o) showDeleteConfirm = false; }}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay class="modal-overlay" />
      <AlertDialog.Content class="modal-content">
        <AlertDialog.Title class="modal-title">Delete Agent</AlertDialog.Title>
        <AlertDialog.Description class="modal-description">
          Are you sure you want to delete "{agent.name}"? The agent will be removed from all views.
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
  .agent-detail {
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

  .header-badges {
    display: flex;
    gap: 0.375rem;
    margin-top: 0.375rem;
  }

  .type-badge {
    display: inline-block;
    font-size: 0.625rem;
    font-weight: 600;
    padding: 0.0625rem 0.375rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .disabled-badge {
    display: inline-block;
    font-size: 0.625rem;
    font-weight: 500;
    padding: 0.0625rem 0.375rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
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
    word-break: break-all;
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
