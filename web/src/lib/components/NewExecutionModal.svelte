<script lang="ts">
  import { Dialog } from 'bits-ui';
  import { agentsQuery } from '../queries/agents';
  import { projectsQuery } from '../queries/projects';
  import { createExecutionMutation } from '../queries/executions';
  import { router } from '../router';
  import Button from './ui/button.svelte';
  import type { ExecutionPrefill } from './ExecutionDetail.svelte';

  interface Props {
    onclose?: () => void;
    initialProjectId?: string | null;
    prefill?: ExecutionPrefill | null;
  }

  let { onclose, initialProjectId = null, prefill = null }: Props = $props();

  const agents = agentsQuery();
  const projects = projectsQuery();
  const createMut = createExecutionMutation();

  let isOpen = $state(true);
  let selectedAgentId = $state('');
  let selectedProjectId = $state(initialProjectId ?? '');
  let task = $state('');
  let title = $state('');
  let branch = $state('');
  let cwd = $state('');
  let showAdvanced = $state(false);
  let error: string | null = $state(null);

  // Apply prefill values once on mount (not on every reactive change)
  let prefillApplied = false;
  $effect.pre(() => {
    if (prefill && !prefillApplied) {
      prefillApplied = true;
      if (prefill.projectId) selectedProjectId = prefill.projectId;
      if (prefill.agentId) selectedAgentId = prefill.agentId;
      if (prefill.prompt) task = prefill.prompt;
      if (prefill.title) title = prefill.title;
    }
  });

  let enabledAgents = $derived((agents.data ?? []).filter(a => a.enabled));
  let projectList = $derived(projects.data ?? []);
  let selectedProject = $derived(projectList.find(p => p.id === selectedProjectId) ?? null);
  let submitting = $derived(createMut.isPending);

  let canSubmit = $derived(
    !!selectedAgentId && task.trim().length > 0 && (!!selectedProjectId || !!cwd.trim()) && !submitting
  );

  // Auto-select agent when only one is available (skip when prefilled)
  $effect(() => {
    if (!prefill && !selectedAgentId && enabledAgents.length === 1) {
      selectedAgentId = enabledAgents[0].id;
    }
  });

  // When project is selected, auto-set agent to project's default and clear branch if non-git (skip when prefilled)
  $effect(() => {
    if (!prefill && selectedProject) {
      if (selectedProject.default_agent_id) {
        const defaultAgent = enabledAgents.find(a => a.id === selectedProject!.default_agent_id);
        if (defaultAgent) {
          selectedAgentId = defaultAgent.id;
        }
      }
      if (!selectedProject.is_git) {
        branch = '';
      }
    }
  });

  // Mutual exclusivity: branch and cwd
  function handleBranchInput(value: string) {
    branch = value;
    if (value.trim()) cwd = '';
  }

  function handleCwdInput(value: string) {
    cwd = value;
    if (value.trim()) branch = '';
  }

  function generateTitle(prompt: string): string {
    const firstLine = prompt.split('\n')[0].trim();
    const match = firstLine.match(/^[^.!?]+[.!?]/);
    const text = match ? match[0] : firstLine;
    if (text.length <= 60) return text;
    const truncated = text.slice(0, 60);
    const lastSpace = truncated.lastIndexOf(' ');
    return lastSpace > 0 ? truncated.slice(0, lastSpace) + '...' : truncated + '...';
  }

  async function handleSubmit() {
    if (!canSubmit) return;
    error = null;

    const prompt = task.trim();
    const req = {
      agent_id: selectedAgentId,
      prompt,
      title: title.trim() || generateTitle(prompt),
      ...(selectedProjectId && { project_id: selectedProjectId }),
      ...(branch.trim() && { branch: branch.trim() }),
      ...(cwd.trim() && { cwd: cwd.trim() }),
    };

    try {
      const result = await createMut.mutateAsync(req);
      handleClose();
      router.navigate(`/execution/${result.execution.id}`);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to create execution';
    }
  }

  function handleClose() {
    isOpen = false;
    onclose?.();
  }
</script>

<Dialog.Root bind:open={isOpen} onOpenChange={(o) => { if (!o) handleClose(); }}>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content" aria-describedby={undefined}>
      <Dialog.Title class="modal-title">{prefill ? 'Re-run Execution' : 'New Execution'}</Dialog.Title>

      <div class="field">
        <label class="field-label" for="exec-project">Project</label>
        <select
          id="exec-project"
          class="field-select"
          bind:value={selectedProjectId}
        >
          <option value="">No project</option>
          {#each projectList as project}
            <option value={project.id}>{project.name}</option>
          {/each}
        </select>
      </div>

      <div class="field">
        <label class="field-label" for="exec-agent">Agent</label>
        <select
          id="exec-agent"
          class="field-select"
          bind:value={selectedAgentId}
        >
          <option value="">Select an agent...</option>
          {#each enabledAgents as agent}
            <option value={agent.id}>{agent.name}</option>
          {/each}
        </select>
      </div>

      <div class="field">
        <label class="field-label" for="exec-task">Task</label>
        <textarea
          id="exec-task"
          class="field-textarea"
          placeholder="Describe what the agent should do..."
          bind:value={task}
          rows="4"
        ></textarea>
      </div>

      <div class="field">
        <label class="field-label" for="exec-title">Title <span class="optional">(optional)</span></label>
        <input
          id="exec-title"
          class="field-input"
          type="text"
          placeholder="Short title for this execution"
          bind:value={title}
        />
      </div>

      <button class="toggle-link" onclick={() => showAdvanced = !showAdvanced}>
        {showAdvanced ? 'Hide' : 'Show'} Advanced
      </button>

      {#if showAdvanced}
        {#if selectedProject?.is_git}
          <div class="field">
            <label class="field-label" for="exec-branch">Branch <span class="optional">(optional)</span></label>
            <input
              id="exec-branch"
              class="field-input"
              type="text"
              placeholder="Optional: explicit branch name"
              value={branch}
              oninput={(e) => handleBranchInput(e.currentTarget.value)}
              disabled={!!cwd.trim()}
            />
            <span class="field-hint">Leave blank for automatic isolated copy</span>
          </div>
        {/if}
        <div class="field">
          <label class="field-label" for="exec-cwd">Working Directory <span class="optional">(optional)</span></label>
          <input
            id="exec-cwd"
            class="field-input"
            type="text"
            placeholder="/absolute/path/to/directory"
            value={cwd}
            oninput={(e) => handleCwdInput(e.currentTarget.value)}
            disabled={!!branch.trim()}
          />
          <span class="field-hint">Overrides project path. Mutually exclusive with branch.</span>
        </div>
      {/if}

      {#if !selectedProjectId && !cwd.trim()}
        <div class="validation-hint">Select a project or specify a working directory.</div>
      {/if}

      {#if error}
        <div class="modal-error">{error}</div>
      {/if}

      <div class="modal-actions">
        <Button variant="ghost" onclick={handleClose}>Cancel</Button>
        <Button variant="default" disabled={!canSubmit} onclick={handleSubmit}>
          {submitting ? 'Starting...' : 'Start'}
        </Button>
      </div>
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<style>
  .field {
    margin-bottom: 1rem;
  }

  .field-label {
    display: block;
    font-size: 0.8125rem;
    font-weight: 500;
    margin-bottom: 0.375rem;
    color: hsl(var(--foreground));
  }

  .optional {
    color: hsl(var(--muted-foreground));
    font-weight: 400;
  }

  .field-select, .field-textarea, .field-input {
    width: 100%;
    padding: 0.5rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.375rem;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: inherit;
  }

  .field-select:focus, .field-textarea:focus, .field-input:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .field-select:disabled, .field-input:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .field-textarea {
    resize: vertical;
    min-height: 5rem;
  }

  .field-hint {
    display: block;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
  }

  .toggle-link {
    background: none;
    border: none;
    font-size: 0.75rem;
    color: hsl(var(--primary));
    cursor: pointer;
    padding: 0;
    margin-bottom: 0.75rem;
  }

  .toggle-link:hover {
    opacity: 0.8;
  }

  .validation-hint {
    font-size: 0.75rem;
    color: hsl(var(--status-attention));
    margin-bottom: 0.75rem;
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
