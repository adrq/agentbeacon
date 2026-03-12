<script lang="ts">
  import { Dialog, AlertDialog } from 'bits-ui';
  import type { McpServer } from '../types';
  import { mcpServersQuery, createMcpServerMutation, updateMcpServerMutation, deleteMcpServerMutation } from '../queries/mcp-servers';
  import Button from './ui/button.svelte';

  const serversQuery = mcpServersQuery();
  const createMut = createMcpServerMutation();
  const updateMut = updateMcpServerMutation();
  const deleteMut = deleteMcpServerMutation();

  let servers = $derived<McpServer[]>(serversQuery.data ?? []);

  // Form state
  let showForm = $state(false);
  let editingServer: McpServer | null = $state(null);
  let formName = $state('');
  let formTransportType = $state<'stdio' | 'http'>('stdio');
  let formCommand = $state('');
  let formArgs = $state('');
  let formEnv = $state('');
  let formUrl = $state('');
  let formHeaders = $state('');
  let formError: string | null = $state(null);

  // Delete state
  let showDeleteConfirm = $state(false);
  let deletingServer: McpServer | null = $state(null);
  let deleteError: string | null = $state(null);

  let submitting = $derived(createMut.isPending || updateMut.isPending);

  function openAddForm() {
    editingServer = null;
    formName = '';
    formTransportType = 'stdio';
    formCommand = '';
    formArgs = '';
    formEnv = '';
    formUrl = '';
    formHeaders = '';
    formError = null;
    showForm = true;
  }

  function openEditForm(server: McpServer) {
    editingServer = server;
    formName = server.name;
    formTransportType = server.transport_type;
    if (server.transport_type === 'stdio') {
      formCommand = (server.config.command as string) ?? '';
      const args = server.config.args;
      formArgs = Array.isArray(args) ? args.join(', ') : '';
      const env = server.config.env;
      formEnv = env && typeof env === 'object' ? JSON.stringify(env, null, 2) : '';
      formUrl = '';
      formHeaders = '';
    } else {
      formUrl = (server.config.url as string) ?? '';
      const headers = server.config.headers;
      formHeaders = headers && typeof headers === 'object' ? JSON.stringify(headers, null, 2) : '';
      formCommand = '';
      formArgs = '';
      formEnv = '';
    }
    formError = null;
    showForm = true;
  }

  function closeForm() {
    showForm = false;
    editingServer = null;
    formError = null;
  }

  function buildConfig(): Record<string, unknown> {
    if (formTransportType === 'stdio') {
      const config: Record<string, unknown> = { command: formCommand.trim() };
      if (formArgs.trim()) {
        config.args = formArgs.split(',').map(a => a.trim()).filter(Boolean);
      }
      if (formEnv.trim()) {
        try {
          config.env = JSON.parse(formEnv.trim());
        } catch {
          throw new Error('Env must be valid JSON');
        }
      }
      return config;
    } else {
      const config: Record<string, unknown> = { url: formUrl.trim() };
      if (formHeaders.trim()) {
        try {
          config.headers = JSON.parse(formHeaders.trim());
        } catch {
          throw new Error('Headers must be valid JSON');
        }
      }
      return config;
    }
  }

  async function handleSubmit() {
    formError = null;
    let config: Record<string, unknown>;
    try {
      config = buildConfig();
    } catch (e) {
      formError = e instanceof Error ? e.message : 'Invalid config';
      return;
    }

    if (!formName.trim()) {
      formError = 'Name is required';
      return;
    }
    if (formTransportType === 'stdio' && !formCommand.trim()) {
      formError = 'Command is required for stdio transport';
      return;
    }
    if (formTransportType === 'http' && !formUrl.trim()) {
      formError = 'URL is required for HTTP transport';
      return;
    }

    try {
      if (editingServer) {
        await updateMut.mutateAsync({
          id: editingServer.id,
          req: {
            name: formName.trim(),
            transport_type: formTransportType,
            config,
          },
        });
      } else {
        await createMut.mutateAsync({
          name: formName.trim(),
          transport_type: formTransportType,
          config,
        });
      }
      closeForm();
    } catch (e) {
      formError = e instanceof Error ? e.message : 'Operation failed';
    }
  }

  function confirmDelete(server: McpServer) {
    deletingServer = server;
    deleteError = null;
    showDeleteConfirm = true;
  }

  async function handleDelete() {
    if (!deletingServer) return;
    deleteError = null;
    try {
      await deleteMut.mutateAsync(deletingServer.id);
      showDeleteConfirm = false;
      deletingServer = null;
    } catch (e) {
      deleteError = e instanceof Error ? e.message : 'Delete failed';
    }
  }

  function transportLabel(type: string): string {
    return type === 'stdio' ? 'stdio' : 'HTTP';
  }

  function serverSummary(server: McpServer): string {
    if (server.transport_type === 'stdio') {
      const cmd = server.config.command as string ?? '';
      const args = server.config.args;
      return Array.isArray(args) ? `${cmd} ${args.join(' ')}` : cmd;
    }
    return (server.config.url as string) ?? '';
  }
</script>

<div class="mcp-section">
  <div class="mcp-header">
    <h3 class="section-heading">MCP Servers</h3>
    <button class="pool-add-btn" onclick={openAddForm}>+ Add</button>
  </div>

  {#if serversQuery.isLoading}
    <p class="mcp-empty">Loading...</p>
  {:else if servers.length === 0}
    <p class="mcp-empty">No MCP servers configured. Add one to make it available to agents.</p>
  {:else}
    <div class="mcp-list">
      {#each servers as server (server.id)}
        <div class="mcp-card">
          <div class="mcp-card-info">
            <span class="mcp-name">{server.name}</span>
            <span class="mcp-type-badge">{transportLabel(server.transport_type)}</span>
            <span class="mcp-summary">{serverSummary(server)}</span>
          </div>
          <div class="mcp-card-actions">
            <button class="mcp-action-btn" onclick={() => openEditForm(server)}>Edit</button>
            <button class="mcp-action-btn mcp-action-danger" onclick={() => confirmDelete(server)}>Delete</button>
          </div>
        </div>
      {/each}
    </div>
  {/if}
</div>

<Dialog.Root bind:open={showForm}>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content">
      <Dialog.Title class="modal-title">
        {editingServer ? 'Edit MCP Server' : 'Add MCP Server'}
      </Dialog.Title>

      <form onsubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
        <div class="form-group">
          <label class="form-label" for="mcp-name">Name</label>
          <input id="mcp-name" class="form-input" type="text" bind:value={formName} placeholder="e.g. playwright" />
        </div>

        <div class="form-group">
          <label class="form-label" for="mcp-transport">Transport</label>
          <select id="mcp-transport" class="form-input" bind:value={formTransportType}>
            <option value="stdio">stdio</option>
            <option value="http">HTTP</option>
          </select>
        </div>

        {#if formTransportType === 'stdio'}
          <div class="form-group">
            <label class="form-label" for="mcp-command">Command</label>
            <input id="mcp-command" class="form-input" type="text" bind:value={formCommand} placeholder="e.g. npx" />
          </div>
          <div class="form-group">
            <label class="form-label" for="mcp-args">Args (comma separated)</label>
            <input id="mcp-args" class="form-input" type="text" bind:value={formArgs} placeholder="e.g. @playwright/mcp" />
          </div>
          <div class="form-group">
            <label class="form-label" for="mcp-env">Env (JSON, optional)</label>
            <textarea id="mcp-env" class="form-textarea" rows="3" bind:value={formEnv} placeholder={'{"DISPLAY": ":1"}'}></textarea>
          </div>
        {:else}
          <div class="form-group">
            <label class="form-label" for="mcp-url">URL</label>
            <input id="mcp-url" class="form-input" type="text" bind:value={formUrl} placeholder="https://example.com/mcp" />
          </div>
          <div class="form-group">
            <label class="form-label" for="mcp-headers">Headers (JSON, optional)</label>
            <textarea id="mcp-headers" class="form-textarea" rows="3" bind:value={formHeaders} placeholder={'{"Authorization": "Bearer ..."}'}></textarea>
          </div>
        {/if}

        {#if formError}
          <div class="form-error" role="alert">{formError}</div>
        {/if}

        <div class="form-actions">
          <Button variant="ghost" size="sm" onclick={closeForm} type="button">Cancel</Button>
          <Button variant="default" size="sm" disabled={submitting} type="submit">
            {submitting ? 'Saving...' : editingServer ? 'Save' : 'Create'}
          </Button>
        </div>
      </form>
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<AlertDialog.Root bind:open={showDeleteConfirm}>
  <AlertDialog.Portal>
    <AlertDialog.Overlay class="modal-overlay" />
    <AlertDialog.Content class="modal-content">
      <AlertDialog.Title class="modal-title">Delete MCP Server</AlertDialog.Title>
      <AlertDialog.Description class="modal-description">
        Are you sure you want to delete "{deletingServer?.name}"?
      </AlertDialog.Description>
      {#if deleteError}
        <div class="form-error" role="alert">{deleteError}</div>
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
  .mcp-section {
    margin-top: 2rem;
  }

  .mcp-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.75rem;
  }

  .mcp-empty {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .mcp-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .mcp-card {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
  }

  .mcp-card-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    min-width: 0;
    flex: 1;
  }

  .mcp-name {
    font-size: 0.8125rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    flex-shrink: 0;
  }

  .mcp-type-badge {
    font-size: 0.625rem;
    font-weight: 600;
    padding: 0.0625rem 0.375rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--primary) / 0.12);
    color: hsl(var(--primary));
    text-transform: uppercase;
    letter-spacing: 0.05em;
    flex-shrink: 0;
  }

  .mcp-summary {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .mcp-card-actions {
    display: flex;
    gap: 0.25rem;
    flex-shrink: 0;
    margin-left: 0.5rem;
  }

  .mcp-action-btn {
    background: none;
    border: none;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    font-size: 0.75rem;
    padding: 0.125rem 0.375rem;
    border-radius: var(--radius-sm);
  }

  .mcp-action-btn:hover {
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--foreground));
  }

  .mcp-action-danger:hover {
    color: hsl(var(--status-danger));
  }

  .pool-add-btn {
    background: none;
    border: none;
    color: hsl(var(--primary));
    cursor: pointer;
    font-size: 0.75rem;
    padding: 0;
  }

  .pool-add-btn:hover {
    opacity: 0.8;
  }

  .section-heading {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .form-group {
    margin-bottom: 0.75rem;
  }

  .form-label {
    display: block;
    font-size: 0.75rem;
    font-weight: 500;
    color: hsl(var(--foreground));
    margin-bottom: 0.25rem;
  }

  .form-input {
    width: 100%;
    padding: 0.375rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
  }

  .form-input:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .form-textarea {
    width: 100%;
    padding: 0.375rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: var(--font-mono);
    resize: vertical;
  }

  .form-textarea:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .form-error {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
    margin-bottom: 0.75rem;
  }

  .form-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
