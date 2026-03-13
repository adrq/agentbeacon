<script lang="ts">
  import { get } from 'svelte/store';
  import { untrack } from 'svelte';
  import { agentsQuery } from '../queries/agents';
  import { projectsQuery } from '../queries/projects';
  import { createExecutionMutation } from '../queries/executions';
  import { router } from '../router';
  import { executionPrefill } from '../stores/appState';
  import { api } from '../api';
  import type { AgentPoolEntry } from '../types';
  import Button from './ui/button.svelte';

  const agents = agentsQuery();
  const projects = projectsQuery();
  const createMut = createExecutionMutation();

  let selectedRootAgentId = $state('');
  let selectedAgentIds = $state<Set<string>>(new Set());
  let selectedProjectId = $state('');
  let task = $state('');
  let title = $state('');
  let branch = $state('');
  let cwd = $state('');
  let maxDepth = $state('');
  let maxWidth = $state('');
  let error: string | null = $state(null);
  let sourceExecutionId: string | undefined = undefined;

  // Apply prefill store once on mount
  let prefillApplied = $state(false);
  let hasPrefill = $state(false);
  $effect.pre(() => {
    if (!prefillApplied) {
      const prefill = get(executionPrefill);
      if (prefill) {
        prefillApplied = true;
        hasPrefill = true;
        sourceExecutionId = prefill.sourceExecutionId;
        if (prefill.projectId) selectedProjectId = prefill.projectId;
        if (prefill.agentId) {
          selectedRootAgentId = prefill.agentId;
          selectedAgentIds = new Set([prefill.agentId]);
        }
        if (prefill.agentIds) {
          selectedAgentIds = new Set(prefill.agentIds);
          if (prefill.agentId && prefill.agentIds.includes(prefill.agentId)) {
            selectedRootAgentId = prefill.agentId;
          }
        }
        if (prefill.prompt) task = prefill.prompt;
        if (prefill.title) title = prefill.title;
        executionPrefill.set(null);
      } else {
        prefillApplied = true;
      }
    }
  });

  let enabledAgents = $derived((agents.data ?? []).filter(a => a.enabled));
  let projectList = $derived(projects.data ?? []);
  let selectedProject = $derived(projectList.find(p => p.id === selectedProjectId) ?? null);
  let submitting = $derived(createMut.isPending);

  let singleAgentMode = $derived(enabledAgents.length === 1);
  let poolAgents = $derived(enabledAgents.filter(a => selectedAgentIds.has(a.id)));

  let canSubmit = $derived(
    !!selectedRootAgentId
    && selectedAgentIds.size > 0
    && selectedAgentIds.has(selectedRootAgentId)
    && task.trim().length > 0
    && (!!selectedProjectId || !!cwd.trim())
    && !submitting
  );

  // Auto-select agent when only one is available (skip when prefilled)
  $effect(() => {
    if (!hasPrefill && enabledAgents.length === 1) {
      const agent = enabledAgents[0];
      selectedRootAgentId = agent.id;
      selectedAgentIds = new Set([agent.id]);
    }
  });

  // When project is selected, fetch project's agent pool
  $effect(() => {
    if (!hasPrefill && selectedProject) {
      api.getProjectAgents(selectedProject.id).then((pool: AgentPoolEntry[]) => {
        if (pool.length > 0) {
          const ids = new Set(pool.map(a => a.agent_id).filter(id =>
            enabledAgents.some(ea => ea.id === id)
          ));
          if (ids.size > 0) {
            selectedAgentIds = ids;
            if (ids.size === 1) {
              selectedRootAgentId = [...ids][0];
            } else if (!ids.has(selectedRootAgentId)) {
              selectedRootAgentId = '';
            }
          }
        }
      }).catch(() => { /* project may not have pool yet */ });

      if (!selectedProject.is_git) {
        branch = '';
      }
    }
  });

  // Baseline snapshots for dirty tracking, split by timing:
  // - textBaseline: captured immediately after prefill (text fields are never auto-populated)
  // - selBaseline: captured after agents.data loads (accounts for auto-select/pool effects)
  let textBaseline = $state<Record<string, string> | null>(null);
  $effect(() => {
    if (textBaseline === null && prefillApplied) {
      textBaseline = untrack(() => ({ task, title, branch, cwd, maxDepth, maxWidth }));
    }
  });

  let selBaseline = $state<Record<string, string> | null>(null);
  $effect(() => {
    if (selBaseline === null && prefillApplied && agents.data) {
      selBaseline = untrack(() => ({
        selectedProjectId,
        selectedRootAgentId,
        agentIds: [...selectedAgentIds].sort().join(','),
      }));
    }
  });

  let isDirty = $derived.by(() => {
    if (!textBaseline) return false;
    const textDirty = (
      task !== textBaseline.task
      || title !== textBaseline.title
      || branch !== textBaseline.branch
      || cwd !== textBaseline.cwd
      || maxDepth !== textBaseline.maxDepth
      || maxWidth !== textBaseline.maxWidth
    );
    if (textDirty) return true;
    if (!selBaseline) return false;
    return (
      selectedProjectId !== selBaseline.selectedProjectId
      || selectedRootAgentId !== selBaseline.selectedRootAgentId
      || [...selectedAgentIds].sort().join(',') !== selBaseline.agentIds
    );
  });

  // Navigation guard — warns on dirty form
  let guardCleanup: (() => void) | null = null;
  let unloadHandler: ((e: BeforeUnloadEvent) => void) | null = null;
  $effect(() => {
    if (isDirty) {
      guardCleanup = router.addNavigationGuard(() => true);
      unloadHandler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
      window.addEventListener('beforeunload', unloadHandler);
    } else {
      guardCleanup?.();
      guardCleanup = null;
      if (unloadHandler) { window.removeEventListener('beforeunload', unloadHandler); unloadHandler = null; }
    }
    return () => {
      guardCleanup?.();
      guardCleanup = null;
      if (unloadHandler) { window.removeEventListener('beforeunload', unloadHandler); unloadHandler = null; }
    };
  });

  function clearGuard() {
    guardCleanup?.();
    guardCleanup = null;
    if (unloadHandler) { window.removeEventListener('beforeunload', unloadHandler); unloadHandler = null; }
  }

  function togglePoolAgent(agentId: string) {
    const next = new Set(selectedAgentIds);
    if (next.has(agentId)) {
      next.delete(agentId);
      if (selectedRootAgentId === agentId) {
        selectedRootAgentId = '';
      }
    } else {
      next.add(agentId);
    }
    selectedAgentIds = next;
  }

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
    const parsedDepth = maxDepth !== '' ? parseInt(String(maxDepth), 10) : undefined;
    const parsedWidth = maxWidth !== '' ? parseInt(String(maxWidth), 10) : undefined;
    const req = {
      root_agent_id: selectedRootAgentId,
      agent_ids: [...selectedAgentIds],
      prompt,
      title: title.trim() || generateTitle(prompt),
      ...(selectedProjectId && { project_id: selectedProjectId }),
      ...(branch.trim() && { branch: branch.trim() }),
      ...(cwd.trim() && { cwd: cwd.trim() }),
      ...(parsedDepth !== undefined && !isNaN(parsedDepth) && { max_depth: parsedDepth }),
      ...(parsedWidth !== undefined && !isNaN(parsedWidth) && { max_width: parsedWidth }),
    };

    try {
      const result = await createMut.mutateAsync(req);
      clearGuard();
      router.navigate(`/execution/${result.execution.id}`);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to create execution';
    }
  }

  function handleCancel() {
    clearGuard();
    if (sourceExecutionId) {
      router.navigate(`/execution/${sourceExecutionId}`);
    } else {
      router.navigate('/executions');
    }
  }
</script>

<div class="form-panel scroll-thin">
  <div class="form-panel-header">
    <h2 class="form-panel-title">{hasPrefill ? 'Re-run Execution' : 'New Execution'}</h2>
    <div class="form-panel-actions">
      <Button variant="ghost" onclick={handleCancel}>Cancel</Button>
      <Button variant="default" disabled={!canSubmit} onclick={handleSubmit}>
        {submitting ? 'Starting...' : 'Start'}
      </Button>
    </div>
  </div>

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

  {#if !singleAgentMode && enabledAgents.length > 1}
    <div class="form-section-label">Agent Pool ({selectedAgentIds.size} selected)</div>
    <div class="pool-section">
      {#each enabledAgents as agent (agent.id)}
        <label class="pool-checkbox">
          <input
            type="checkbox"
            checked={selectedAgentIds.has(agent.id)}
            onchange={() => togglePoolAgent(agent.id)}
          />
          <span>{agent.name}</span>
        </label>
      {/each}
    </div>
  {/if}

  <div class="field">
    <label class="field-label" for="exec-agent">Root Agent</label>
    <select
      id="exec-agent"
      class="field-select"
      bind:value={selectedRootAgentId}
    >
      <option value="">Select root agent...</option>
      {#each poolAgents as agent}
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

  <hr class="form-section-divider" />
  <div class="form-section-label">Advanced</div>

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
  <div class="field">
    <label class="field-label" for="exec-max-depth">Max Depth <span class="optional">(default: 2)</span></label>
    <input
      id="exec-max-depth"
      class="field-input"
      type="number"
      min="1"
      max="10"
      placeholder="2"
      bind:value={maxDepth}
    />
    <span class="field-hint">Maximum delegation depth (1 = flat, no sub-leads)</span>
  </div>
  <div class="field">
    <label class="field-label" for="exec-max-width">Max Width <span class="optional">(default: 5)</span></label>
    <input
      id="exec-max-width"
      class="field-input"
      type="number"
      min="1"
      max="50"
      placeholder="5"
      bind:value={maxWidth}
    />
    <span class="field-hint">Maximum active children per agent</span>
  </div>

  {#if enabledAgents.length === 0}
    <div class="validation-hint">No enabled agents available.</div>
  {:else if !selectedProjectId && !cwd.trim()}
    <div class="validation-hint">Select a project or specify a working directory.</div>
  {/if}

  {#if error}
    <div class="form-error" role="alert">{error}</div>
  {/if}
</div>
