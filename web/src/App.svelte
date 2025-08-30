<script lang="ts">
  import WorkflowEditor from './lib/components/WorkflowEditor.svelte';
  import DAGVisualization from './lib/components/DAGVisualization.svelte';
  import ExecutionControls from './lib/components/ExecutionControls.svelte';
  import OutputPanel from './lib/components/OutputPanel.svelte';
  import { environment } from './lib/adapters/index.js';
  import { onMount } from 'svelte';
  import Card from './lib/components/ui/card.svelte';
  import CardHeader from './lib/components/ui/card-header.svelte';
  import CardContent from './lib/components/ui/card-content.svelte';
  import Button from './lib/components/ui/button.svelte';
  import ThemeToggle from './lib/components/ThemeToggle.svelte';

  let currentWorkflow: string = '';
  let isExecuting = false;
  let showOutput = false;

  onMount(() => {
    environment.showNotification('AgentMaestro started', 'info');
  });

  function handleWorkflowChange(event: CustomEvent<string>) {
    currentWorkflow = event.detail;
  }
  function handleExecutionStart() { isExecuting = true; showOutput = true; environment.showNotification('Workflow execution started', 'info'); }
  function handleExecutionStop() { isExecuting = false; environment.showNotification('Workflow execution stopped', 'info'); }
  function toggleOutput() { showOutput = !showOutput; }
</script>
<div class="app-shell">
  <header class="app-header">
    <h1 class="text-xl font-semibold tracking-tight text-primary">AgentMaestro</h1>
    <div class="flex items-center gap-3">
      <div class="text-xs px-3 py-1 rounded-full bg-secondary text-secondary-foreground font-medium">{environment.name}</div>
      <ThemeToggle />
    </div>
  </header>
  <main class="flex-1 flex flex-col overflow-hidden">
    <!-- Top 2-column region per technical requirements (3.1 Layout) -->
    <div class="flex-1 grid gap-4 p-4 md:grid-cols-2 grid-cols-1 auto-rows-[minmax(0,1fr)] min-h-0">
      <Card className="min-h-0 card-elevated">
        <CardHeader className="py-3">
          <h2 class="section-heading">Workflow Editor</h2>
        </CardHeader>
        <CardContent>
          <WorkflowEditor bind:value={currentWorkflow} on:change={handleWorkflowChange} />
        </CardContent>
      </Card>
      <Card className="min-h-0 card-elevated">
        <CardHeader className="py-3">
          <h2 class="section-heading">DAG Visualization</h2>
        </CardHeader>
        <CardContent>
          <DAGVisualization workflow={currentWorkflow} {isExecuting} />
        </CardContent>
      </Card>
    </div>
    <!-- Execution controls spanning full width -->
    <div class="border-t bg-card/80 backdrop-blur px-4 py-3 flex items-center justify-between gap-4">
      <ExecutionControls {currentWorkflow} {isExecuting} on:start={handleExecutionStart} on:stop={handleExecutionStop} />
      <Button variant="outline" size="sm" on:click={toggleOutput} aria-expanded={showOutput}>{showOutput ? 'Hide' : 'Show'} Output</Button>
    </div>
    <!-- Collapsible Output bottom panel -->
    <div class="relative overflow-hidden transition-[max-height] duration-300 ease-out bg-black/60 border-t" style:max-height={showOutput ? '320px' : '0'} aria-hidden={!showOutput}>
      {#if showOutput}
        <div class="h-80 flex flex-col">
          <div class="flex-1 min-h-0">
            <OutputPanel {isExecuting} />
          </div>
        </div>
      {/if}
    </div>
  </main>
</div>
