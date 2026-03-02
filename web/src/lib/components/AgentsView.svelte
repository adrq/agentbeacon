<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import { agentsQuery, deleteAgentMutation, driversQuery, createDriverMutation } from '../queries/agents';
  import type { Agent } from '../types';
  import Button from './ui/button.svelte';
  import AgentForm from './AgentForm.svelte';

  const agents = agentsQuery();
  const drivers = driversQuery();
  const deleteMut = deleteAgentMutation();
  const createDriverMut = createDriverMutation();

  let showCreateForm = $state(false);
  let editingAgent = $state<Agent | null>(null);
  let deletingAgent = $state<Agent | null>(null);
  let deleteError: string | null = $state(null);

  interface AgentTemplate {
    name: string;
    platform: string;
    description: string;
    config: Record<string, unknown>;
  }

  const templates: AgentTemplate[] = [
    {
      name: 'Claude Code',
      platform: 'claude_sdk',
      description: 'Claude Code via Agent SDK',
      config: { command: 'claude', args: [], timeout: 300, env: {}, state_dir: '~/.claude' },
    },
    {
      name: 'Copilot',
      platform: 'copilot_sdk',
      description: 'GitHub Copilot Coding Agent',
      config: { command: 'copilot-agent', args: [], timeout: 300, env: {} },
    },
    {
      name: 'Codex',
      platform: 'codex_sdk',
      description: 'OpenAI Codex CLI Agent',
      config: { command: 'codex', args: [], timeout: 300, env: {} },
    },
    {
      name: 'OpenCode',
      platform: 'opencode_sdk',
      description: 'OpenCode Agent',
      config: { command: 'opencode', args: [], timeout: 300, env: {} },
    },
  ];

  let templateForCreate = $state<AgentTemplate | null>(null);
  let resolvedDriverId = $state<string | null>(null);

  async function handleTemplateClick(template: AgentTemplate) {
    // Find or auto-create driver for this platform
    const existing = (drivers.data ?? []).find(d => d.platform === template.platform);
    if (existing) {
      resolvedDriverId = existing.id;
    } else {
      try {
        const created = await createDriverMut.mutateAsync({
          name: template.platform,
          platform: template.platform,
        });
        resolvedDriverId = created.id;
      } catch (e) {
        console.error('Failed to create driver for template:', e);
        resolvedDriverId = null;
      }
    }
    templateForCreate = template;
    showCreateForm = true;
  }

  function handleCreateClose() {
    showCreateForm = false;
    templateForCreate = null;
    resolvedDriverId = null;
    editingAgent = null;
  }

  function handleFormSubmit() {
    handleCreateClose();
  }

  async function handleDelete() {
    if (!deletingAgent) return;
    deleteError = null;
    try {
      await deleteMut.mutateAsync(deletingAgent.id);
      deletingAgent = null;
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to delete';
      if (msg.includes('409')) {
        deleteError = 'Cannot delete agent with active sessions.';
      } else {
        deleteError = msg;
      }
    }
  }

  const typeLabels: Record<string, string> = {
    claude_sdk: 'Claude',
    codex_sdk: 'Codex',
    copilot_sdk: 'Copilot',
    opencode_sdk: 'OpenCode',
    acp: 'ACP',
    a2a: 'A2A',
  };
</script>

<div class="agents-view scroll-thin">
  <div class="view-header">
    <h2 class="view-title">Agents</h2>
    <Button variant="default" size="sm" onclick={() => showCreateForm = true}>
      Add Agent
    </Button>
  </div>

  {#if agents.isLoading}
    <div class="view-message">Loading agents...</div>
  {:else if agents.isError}
    <div class="view-message view-error">{agents.error?.message ?? 'Failed to load'}</div>
  {:else if (agents.data ?? []).length === 0}
    <div class="empty-state">
      <p class="empty-title">No agents configured</p>
      <p class="empty-description">Add an agent to start running executions. Use a template to get started quickly.</p>
      <div class="template-buttons">
        {#each templates as t}
          <Button variant="ghost" size="sm" onclick={() => handleTemplateClick(t)}>
            + {t.name}
          </Button>
        {/each}
      </div>
    </div>
  {:else}
    <div class="template-row">
      <span class="template-label">Quick add:</span>
      {#each templates as t}
        <button class="template-chip" onclick={() => handleTemplateClick(t)}>
          + {t.name}
        </button>
      {/each}
    </div>

    <div class="agents-grid">
      {#each agents.data ?? [] as agent (agent.id)}
        <div class="agent-card" class:disabled={!agent.enabled}>
          <div class="card-top">
            <span class="card-name">{agent.name}</span>
            <span class="type-badge">{typeLabels[agent.agent_type] ?? agent.agent_type}</span>
            {#if !agent.enabled}
              <span class="disabled-badge">disabled</span>
            {/if}
          </div>
          {#if agent.description}
            <div class="card-description">{agent.description}</div>
          {/if}
          <div class="card-actions">
            <button class="action-link" onclick={() => { editingAgent = agent; showCreateForm = true; }}>Edit</button>
            <button class="action-link danger" onclick={() => { deleteError = null; deletingAgent = agent; }}>Delete</button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

{#if showCreateForm}
  <AgentForm
    agent={editingAgent}
    template={templateForCreate}
    driverId={resolvedDriverId}
    onsubmit={handleFormSubmit}
    oncancel={handleCreateClose}
  />
{/if}

<AlertDialog.Root open={!!deletingAgent} onOpenChange={(o) => { if (!o) deletingAgent = null; }}>
  <AlertDialog.Portal>
    <AlertDialog.Overlay class="modal-overlay" />
    <AlertDialog.Content class="modal-content">
      <AlertDialog.Title class="modal-title">Delete Agent</AlertDialog.Title>
      <AlertDialog.Description class="modal-description">
        Are you sure you want to delete "{deletingAgent?.name}"? The agent will be removed from all views.
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

<style>
  .agents-view {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
  }

  .view-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 1rem;
  }

  .view-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .view-message {
    text-align: center;
    padding: 2rem;
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
  }

  .view-error {
    color: hsl(var(--status-danger));
  }

  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.75rem;
    padding: 3rem 2rem;
    border: 1px dashed hsl(var(--border));
    border-radius: var(--radius);
    text-align: center;
  }

  .empty-title {
    font-size: 1rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .empty-description {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    max-width: 24rem;
  }

  .template-buttons {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
    justify-content: center;
  }

  .template-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 1rem;
    flex-wrap: wrap;
  }

  .template-label {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .template-chip {
    padding: 0.1875rem 0.5rem;
    font-size: 0.6875rem;
    font-weight: 500;
    border: 1px solid hsl(var(--border));
    border-radius: 0.25rem;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
  }

  .template-chip:hover {
    border-color: hsl(var(--primary));
    color: hsl(var(--primary));
  }

  .agents-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
    gap: 0.75rem;
  }

  .agent-card {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 1rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
  }

  .agent-card.disabled {
    opacity: 0.6;
  }

  .card-top {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .card-name {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .type-badge {
    font-size: 0.625rem;
    font-weight: 600;
    padding: 0.0625rem 0.375rem;
    border-radius: 0.25rem;
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .disabled-badge {
    font-size: 0.625rem;
    font-weight: 500;
    padding: 0.0625rem 0.375rem;
    border-radius: 0.25rem;
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
  }

  .card-description {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .card-actions {
    display: flex;
    gap: 0.75rem;
    margin-top: 0.5rem;
  }

  .action-link {
    background: none;
    border: none;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--primary));
    cursor: pointer;
    padding: 0;
    transition: opacity 0.15s;
  }

  .action-link:hover {
    opacity: 0.8;
  }

  .action-link.danger {
    color: hsl(var(--status-danger));
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
