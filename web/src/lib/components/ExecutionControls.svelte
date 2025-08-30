<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { api, type Workflow } from '../api.js';
  import Button from './ui/button.svelte';
  import Select from './ui/select.svelte';

  export let currentWorkflow: string = '';
  export let isExecuting: boolean = false;

  const dispatch = createEventDispatcher<{
    start: void;
    stop: void;
  }>();

  let workflows: Workflow[] = [];
  let selectedWorkflowId: string = '';
  let loadingWorkflows = false;
  let savingWorkflow = false;
  let executingWorkflow = false;

  async function loadWorkflows() {
    loadingWorkflows = true;
    try {
      workflows = await api.getWorkflows();
    } catch (error) {
      console.error('Failed to load workflows:', error);
    } finally {
      loadingWorkflows = false;
    }
  }

  async function saveWorkflow() {
    if (!currentWorkflow.trim()) return;

    savingWorkflow = true;
    try {
      // Extract name from YAML (basic parsing)
      const nameMatch = currentWorkflow.match(/name:\s*['"](.*?)['"]|name:\s*([^\n]+)/);
      const name = nameMatch ? (nameMatch[1] || nameMatch[2]).trim() : 'Untitled Workflow';

      const workflow = await api.createWorkflow({
        name,
        yaml_source: currentWorkflow
      });

      workflows = [...workflows, workflow];
      selectedWorkflowId = workflow.id;
    } catch (error) {
      console.error('Failed to save workflow:', error);
    } finally {
      savingWorkflow = false;
    }
  }

  async function executeWorkflow() {
    if (!selectedWorkflowId) return;

    executingWorkflow = true;
    try {
      await api.startExecution(selectedWorkflowId);
      dispatch('start');
    } catch (error) {
      console.error('Failed to start execution:', error);
    } finally {
      executingWorkflow = false;
    }
  }

  async function stopExecution() {
    // For demo purposes, we'll just dispatch stop
    // In reality, we'd need the execution ID to stop
    dispatch('stop');
  }

  // Load workflows on component mount
  loadWorkflows();
</script>
<div class="flex items-center gap-8 max-xl:gap-4 max-lg:flex-wrap max-sm:flex-col max-sm:items-stretch">
  <div class="flex items-center gap-2 flex-wrap">
    <label for="workflow-select" class="text-xs font-medium text-muted-foreground">Workflow:</label>
    <Select
      id="workflow-select"
      bind:value={selectedWorkflowId}
      disabled={loadingWorkflows || isExecuting}
      items={(workflows ?? []).map(w => ({label: w.name, value: w.id}))}
      placeholder="Select a workflow..."
      on:change={(e)=> selectedWorkflowId = e.detail}
      className="min-w-[200px]"
    />
    <Button variant="secondary" size="md" on:click={loadWorkflows} disabled={loadingWorkflows}>
      {loadingWorkflows ? '↻' : '🔄'} Refresh
    </Button>
  </div>
  <div class="flex items-center gap-2">
    <Button variant="secondary" on:click={saveWorkflow} disabled={savingWorkflow || !currentWorkflow.trim()}>
      {savingWorkflow ? 'Saving...' : '💾 Save'}
    </Button>
    {#if !isExecuting}
      <Button on:click={executeWorkflow} disabled={executingWorkflow || !selectedWorkflowId}>
        {executingWorkflow ? 'Starting...' : '▶️ Run'}
      </Button>
    {:else}
      <Button variant="destructive" on:click={stopExecution}>⏹️ Stop</Button>
    {/if}
  </div>
</div>
