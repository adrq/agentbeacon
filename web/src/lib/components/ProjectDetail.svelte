<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import { projectDetailQuery, deleteProjectMutation, projectAgentsQuery, addProjectAgentMutation, removeProjectAgentMutation } from '../queries/projects';
  import { agentsQuery } from '../queries/agents';
  import { mcpServersQuery, projectMcpServersQuery, addProjectMcpServerMutation, removeProjectMcpServerMutation } from '../queries/mcp-servers';
  import type { AgentPoolEntry, McpServerPoolEntry, Execution, SessionSummary, ExecutionDisplayStatus } from '../types';
  import { executionsQuery, executionDetailQuery } from '../queries/executions';
  import { router } from '../router';
  import Button from './ui/button.svelte';
  import ExecutionListItem from './ExecutionListItem.svelte';
  import StatusBadge from './StatusBadge.svelte';
  import { openSearchTab } from '../stores/wikiState.svelte';
  import { api } from '../api';

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

  // --- Worktree section state ---
  interface WorktreeRow {
    executionId: string;
    title: string;
    sessionId: string;
    branch: string | null;
    status: ExecutionDisplayStatus;
    isTerminal: boolean;
  }

  let worktreeRows = $state<WorktreeRow[]>([]);
  let worktreeLoading = $state(false);
  let selectedWorktrees = $state<Set<string>>(new Set());
  let worktreeLoadId = $state(0);

  // Fingerprint: re-derive worktree data when the execution list changes
  // (new/removed executions or status changes). The underlying query already
  // polls, so this reacts to data the page already fetches.
  let execFingerprint = $derived(
    executions.map(e => `${e.id}:${e.status}`).join(',')
  );
  let lastExecFingerprint = $state('');

  $effect(() => {
    const fp = execFingerprint;
    if (fp === lastExecFingerprint) return;
    lastExecFingerprint = fp;
    const execs = executions;
    if (execs.length === 0) {
      worktreeRows = [];
      return;
    }
    loadWorktreeRows(execs);
  });

  async function loadWorktreeRows(execs: Execution[]) {
    const requestId = ++worktreeLoadId;
    worktreeLoading = true;
    try {
      const rows: WorktreeRow[] = [];
      const results = await Promise.allSettled(
        execs.map(e => api.getExecution(e.id))
      );
      if (requestId !== worktreeLoadId) return;
      for (let i = 0; i < results.length; i++) {
        const result = results[i];
        if (result.status !== 'fulfilled') continue;
        const detail = result.value;
        for (const session of detail.sessions) {
          if (session.worktree_path) {
            const isTerminal = session.outcome != null && session.worker_id == null && session.command_type == null;
            rows.push({
              executionId: execs[i].id,
              title: execs[i].title ?? execs[i].id.slice(0, 8),
              sessionId: session.id,
              branch: null,
              status: execs[i].status,
              isTerminal,
            });
          }
        }
      }
      worktreeRows = rows;
      const validIds = new Set(rows.filter(r => r.isTerminal).map(r => r.sessionId));
      selectedWorktrees = new Set([...selectedWorktrees].filter(id => validIds.has(id)));

      // Fetch branch info in parallel (best-effort)
      const branchResults = await Promise.allSettled(
        rows.map(r => api.getSessionWorktree(r.sessionId))
      );
      if (requestId !== worktreeLoadId) return;
      const updated = [...rows];
      for (let i = 0; i < branchResults.length; i++) {
        const br = branchResults[i];
        if (br.status === 'fulfilled') {
          updated[i] = { ...updated[i], branch: br.value.branch };
        }
      }
      worktreeRows = updated;
    } finally {
      if (requestId === worktreeLoadId) worktreeLoading = false;
    }
  }

  function toggleWorktreeSelection(sessionId: string) {
    const next = new Set(selectedWorktrees);
    if (next.has(sessionId)) {
      next.delete(sessionId);
    } else {
      next.add(sessionId);
    }
    selectedWorktrees = next;
  }

  let hasSelection = $derived(selectedWorktrees.size > 0);

  // --- Worktree delete confirmation dialog ---
  let showWorktreeConfirm = $state(false);
  let worktreeDeleteLoading = $state(false);
  let worktreeDeleteError: string | null = $state(null);
  let deleteBranches = $state(false);

  interface DryRunInfo {
    sessionId: string;
    title: string;
    dirty: boolean | null;
    dirtySummary: string | null;
    branch: string | null;
    directoryMissing: boolean;
  }
  let dryRunResults = $state<DryRunInfo[]>([]);
  let dryRunLoading = $state(false);

  let dirtyCount = $derived(dryRunResults.filter(r => r.dirty === true).length);
  let missingCount = $derived(dryRunResults.filter(r => r.directoryMissing).length);
  let uninspectableCount = $derived(dryRunResults.filter(r => r.dirty === null && !r.directoryMissing).length);
  let hasBranches = $derived(dryRunResults.some(r => r.branch != null));

  async function handleDeleteSelectedClick() {
    worktreeDeleteError = null;
    deleteBranches = false;
    dryRunResults = [];
    dryRunLoading = true;
    showWorktreeConfirm = true;

    try {
      const selected = worktreeRows.filter(r => selectedWorktrees.has(r.sessionId));
      const results = await Promise.allSettled(
        selected.map(r => api.deleteSessionWorktree(r.sessionId, { dryRun: true }))
      );
      const infos: DryRunInfo[] = [];
      for (let i = 0; i < results.length; i++) {
        const res = results[i];
        if (res.status === 'fulfilled') {
          const d = res.value;
          infos.push({
            sessionId: selected[i].sessionId,
            title: selected[i].title,
            dirty: d.dirty as boolean | null,
            dirtySummary: d.dirty_summary as string | null,
            branch: d.branch as string | null,
            directoryMissing: d.directory_missing as boolean,
          });
        } else {
          infos.push({
            sessionId: selected[i].sessionId,
            title: selected[i].title,
            dirty: null,
            dirtySummary: null,
            branch: null,
            directoryMissing: false,
          });
        }
      }
      dryRunResults = infos;
    } finally {
      dryRunLoading = false;
    }
  }

  let worktreeDeleteWarning: string | null = $state(null);

  async function handleWorktreeDelete() {
    worktreeDeleteError = null;
    worktreeDeleteWarning = null;
    worktreeDeleteLoading = true;
    try {
      const results = await Promise.allSettled(
        dryRunResults.map(r =>
          api.deleteSessionWorktree(r.sessionId, { deleteBranch: deleteBranches })
        )
      );

      const failedTitles: string[] = [];
      for (let i = 0; i < results.length; i++) {
        if (results[i].status === 'rejected') {
          failedTitles.push(dryRunResults[i].title);
        }
      }

      // Check for branch deletion failures when user requested it
      if (deleteBranches) {
        const branchFailures = results.filter(
          r => r.status === 'fulfilled' && (r.value as Record<string, unknown>).branch_deleted === false
        );
        if (branchFailures.length > 0) {
          worktreeDeleteWarning = 'Worktree deleted but branch could not be removed';
        }
      }

      // Always refresh — some may have succeeded even if others failed
      showWorktreeConfirm = false;
      selectedWorktrees = new Set();
      loadWorktreeRows(executions);

      if (failedTitles.length > 0) {
        worktreeDeleteError = `Failed to delete: ${failedTitles.join(', ')}`;
      }
    } finally {
      worktreeDeleteLoading = false;
    }
  }

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

    <div class="worktrees-section">
      <div class="worktrees-header">
        <h3 class="section-heading">Worktrees</h3>
        {#if hasSelection}
          <Button variant="ghost" size="sm" onclick={handleDeleteSelectedClick}>Delete selected</Button>
        {/if}
      </div>
      {#if worktreeRows.length === 0}
        <p class="empty-text">No active worktrees</p>
      {:else}
        <table class="wt-table">
          <thead>
            <tr>
              <th class="wt-col-check"></th>
              <th class="wt-col-title">Execution</th>
              <th class="wt-col-branch">Branch</th>
              <th class="wt-col-status">Status</th>
            </tr>
          </thead>
          <tbody>
            {#each worktreeRows as row (row.sessionId)}
              <tr class:wt-row-disabled={!row.isTerminal}>
                <td class="wt-col-check">
                  {#if row.isTerminal}
                    <input
                      type="checkbox"
                      checked={selectedWorktrees.has(row.sessionId)}
                      onchange={() => toggleWorktreeSelection(row.sessionId)}
                    />
                  {/if}
                </td>
                <td class="wt-col-title">{row.title}</td>
                <td class="wt-col-branch">
                  {#if row.branch}
                    <span class="wt-branch">{row.branch}</span>
                  {:else}
                    <span class="wt-branch-none">&mdash;</span>
                  {/if}
                </td>
                <td class="wt-col-status">
                  <StatusBadge status={row.status} size="small" />
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
      {#if worktreeDeleteError}
        <div class="wt-error-toast" role="alert">
          {worktreeDeleteError}
          <button class="wt-warning-dismiss" onclick={() => worktreeDeleteError = null}>&times;</button>
        </div>
      {/if}
      {#if worktreeDeleteWarning}
        <div class="wt-warning-toast" role="status">
          {worktreeDeleteWarning}
          <button class="wt-warning-dismiss" onclick={() => worktreeDeleteWarning = null}>&times;</button>
        </div>
      {/if}
    </div>
  </div>

  <!-- Delete Project Dialog -->
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

  <!-- Delete Worktrees Confirmation Dialog -->
  <AlertDialog.Root bind:open={showWorktreeConfirm}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay class="modal-overlay" />
      <AlertDialog.Content class="modal-content">
        <AlertDialog.Title class="modal-title">Delete Worktrees</AlertDialog.Title>
        <AlertDialog.Description class="modal-description">
          {#if dryRunLoading}
            Checking worktree status...
          {:else}
            Deleting {dryRunResults.length} worktree{dryRunResults.length === 1 ? '' : 's'}:
            <ul class="wt-confirm-list">
              {#each dryRunResults as info (info.sessionId)}
                <li>{info.title}</li>
              {/each}
            </ul>
          {/if}
        </AlertDialog.Description>

        {#if !dryRunLoading}
          {#if dirtyCount > 0}
            <div class="wt-dirty-warning" role="alert">
              {dirtyCount} of {dryRunResults.length} selected worktree{dryRunResults.length === 1 ? '' : 's'} {dirtyCount === 1 ? 'has' : 'have'} uncommitted changes. These changes will be lost.
            </div>
          {/if}

          {#if uninspectableCount > 0}
            <div class="wt-dirty-warning" role="alert">
              Could not inspect {uninspectableCount} worktree{uninspectableCount === 1 ? '' : 's'} — proceed with caution.
            </div>
          {/if}

          {#if missingCount > 0}
            <div class="wt-missing-note">
              {missingCount} worktree{missingCount === 1 ? '' : 's'} already removed from disk.
            </div>
          {/if}

          {#if hasBranches}
            {@const branchNames = [...new Set(dryRunResults.filter(r => r.branch != null).map(r => r.branch as string))].sort((a, b) => a.length - b.length)}
            <label class="wt-branch-checkbox">
              <input type="checkbox" bind:checked={deleteBranches} />
              Also delete associated branches:
            </label>
            {#if deleteBranches}
              <div class="wt-branch-list">
                {#each branchNames as name}
                  <span class="wt-branch-name">{name}</span>
                {/each}
              </div>
              <div class="wt-branch-danger">Branch deletion is permanent and cannot be undone.</div>
            {/if}
          {/if}
        {/if}

        {#if worktreeDeleteError}
          <div class="modal-error" role="alert">{worktreeDeleteError}</div>
        {/if}
        <div class="modal-actions">
          <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Cancel</AlertDialog.Cancel>
          <AlertDialog.Action
            class="alert-btn alert-btn-danger"
            onclick={handleWorktreeDelete}
            disabled={dryRunLoading || worktreeDeleteLoading}
          >
            {worktreeDeleteLoading ? 'Deleting...' : 'Delete'}
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

  /* Worktrees section */
  .worktrees-section {
    margin-top: 1.5rem;
  }

  .worktrees-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.25rem;
  }

  .wt-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8125rem;
  }

  .wt-table th {
    text-align: left;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    padding: 0.375rem 0.5rem;
    border-bottom: 1px solid hsl(var(--border));
  }

  .wt-table td {
    padding: 0.375rem 0.5rem;
    border-bottom: 1px solid hsl(var(--border) / 0.5);
    color: hsl(var(--foreground));
  }

  .wt-col-check {
    width: 2rem;
  }

  .wt-col-branch {
    width: 12rem;
  }

  .wt-col-status {
    width: 6rem;
  }

  .wt-row-disabled td {
    opacity: 0.5;
  }

  .wt-branch {
    font-family: var(--font-mono);
    font-size: 0.625rem;
    font-weight: 500;
  }

  .wt-branch-none {
    color: hsl(var(--muted-foreground));
  }

  /* Worktree confirmation dialog */
  .wt-confirm-list {
    margin: 0.5rem 0 0 1rem;
    padding: 0;
    font-size: 0.8125rem;
  }

  .wt-dirty-warning {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-attention) / 0.1);
    color: hsl(var(--status-attention));
    font-size: 0.8125rem;
    margin-bottom: 0.75rem;
  }

  .wt-missing-note {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
    font-size: 0.8125rem;
    margin-bottom: 0.75rem;
  }

  .wt-branch-checkbox {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
    margin-bottom: 0.375rem;
    cursor: pointer;
  }

  .wt-branch-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    margin: 0 0 0.5rem 1.5rem;
  }

  .wt-branch-name {
    font-family: var(--font-mono);
    font-size: 0.875rem;
    font-weight: 600;
  }

  .wt-branch-danger {
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
    margin-bottom: 0.75rem;
  }

  .wt-error-toast {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
    margin-top: 0.5rem;
  }

  .wt-warning-toast {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-attention) / 0.1);
    color: hsl(var(--status-attention));
    font-size: 0.8125rem;
    margin-top: 0.5rem;
  }

  .wt-warning-dismiss {
    background: none;
    border: none;
    color: inherit;
    cursor: pointer;
    font-size: 1rem;
    line-height: 1;
    padding: 0;
    opacity: 0.7;
  }

  .wt-warning-dismiss:hover {
    opacity: 1;
  }
</style>
