<script lang="ts">
  import WorkflowEditor from './lib/components/WorkflowEditor.svelte';
  import DAGVisualization from './lib/components/DAGVisualization.svelte';
  import ErrorPanel from './lib/components/ErrorPanel.svelte';
  import ExecutionControls from './lib/components/ExecutionControls.svelte';
  import OutputPanel from './lib/components/OutputPanel.svelte';
  import StatusIndicator from './lib/components/StatusIndicator.svelte';
  import { environment } from './lib/adapters/index.js';
  import { api } from './lib/api';
  import { onMount } from 'svelte';
  import Card from './lib/components/ui/card.svelte';
  import CardHeader from './lib/components/ui/card-header.svelte';
  import CardContent from './lib/components/ui/card-content.svelte';
  import Button from './lib/components/ui/button.svelte';
  import ThemeToggle from './lib/components/ThemeToggle.svelte';

  let currentWorkflow: string = '';
  let isExecuting = false;
  let showOutput = false;

  // Workflow UI state
  let validating = false;
  let isValid = false;
  let validationErrors: Array<{
    type: 'syntax' | 'structural' | 'semantic';
    message: string;
    line?: number;
    node?: string;
    nodes?: string[];
  }> = [];
  let theme: 'dark' | 'light' = 'dark';

  onMount(() => {
    environment.showNotification('AgentMaestro started', 'info');

    // Detect system theme
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    theme = prefersDark ? 'dark' : 'light';

    // Listen for theme changes
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleThemeChange = (e: MediaQueryListEvent) => {
      theme = e.matches ? 'dark' : 'light';
    };
    mediaQuery.addEventListener('change', handleThemeChange);

    return () => {
      mediaQuery.removeEventListener('change', handleThemeChange);
    };
  });

  function handleWorkflowChange(event: CustomEvent<string>) {
    currentWorkflow = event.detail;
  }

  async function handleValidate() {
    validating = true;
    validationErrors = [];
    // Reset isValid to false to ensure reactive statements trigger on success
    // This fixes the bug where re-validating doesn't update the DAG
    isValid = false;

    try {
      const result = await api.validateWorkflow(currentWorkflow);

      if (result.valid) {
        isValid = true;
        validationErrors = [];
      } else {
        isValid = false;
        validationErrors = result.errors;
      }
    } catch (error) {
      isValid = false;
      validationErrors = [
        {
          type: 'syntax',
          message: error instanceof Error ? error.message : 'Unknown validation error',
        },
      ];
    } finally {
      validating = false;
    }
  }

  function handleLoadSample() {
    // Sample workflow is loaded by WorkflowEditor component
    // Reset validation state
    isValid = false;
    validationErrors = [];
  }

  function handleExecutionStart() {
    isExecuting = true;
    showOutput = true;
    environment.showNotification('Workflow execution started', 'info');
  }

  function handleExecutionStop() {
    isExecuting = false;
    environment.showNotification('Workflow execution stopped', 'info');
  }

  function toggleOutput() {
    showOutput = !showOutput;
  }
</script>

<div class="app-shell">
  <header class="app-header">
    <h1 class="text-xl font-semibold tracking-tight text-primary">AgentMaestro</h1>
    <div class="flex items-center gap-3">
      <StatusIndicator />
      <div class="text-xs px-3 py-1 rounded-full bg-secondary text-secondary-foreground font-medium">
        {environment.name}
      </div>
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
        <CardContent className="flex flex-col gap-2">
          <WorkflowEditor
            bind:value={currentWorkflow}
            {theme}
            {validating}
            on:change={handleWorkflowChange}
            on:validate={handleValidate}
            on:loadSample={handleLoadSample}
          />
          <ErrorPanel errors={validationErrors} {theme} visible={validationErrors.length > 0} />
        </CardContent>
      </Card>
      <Card className="min-h-0 card-elevated">
        <CardHeader className="py-3">
          <h2 class="section-heading">DAG Visualization</h2>
        </CardHeader>
        <CardContent>
          <DAGVisualization workflow={currentWorkflow} {isValid} {theme} />
        </CardContent>
      </Card>
    </div>
    <!-- Execution controls spanning full width -->
    <div class="border-t bg-card/80 backdrop-blur px-4 py-3 flex items-center justify-between gap-4">
      <ExecutionControls
        {currentWorkflow}
        {isExecuting}
        on:start={handleExecutionStart}
        on:stop={handleExecutionStop}
      />
      <Button variant="outline" size="sm" on:click={toggleOutput} aria-expanded={showOutput}>
        {showOutput ? 'Hide' : 'Show'} Output
      </Button>
    </div>
    <!-- Collapsible Output bottom panel -->
    <div
      class="relative overflow-hidden transition-[max-height] duration-300 ease-out bg-black/60 border-t"
      style:max-height={showOutput ? '320px' : '0'}
      aria-hidden={!showOutput}
    >
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
