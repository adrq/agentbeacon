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
  let validatedWorkflow: string = ''; // Only updated on successful validation
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
  let errorPanelElement: HTMLDivElement | null = null;

  // Resizable panels state
  let leftPanelWidth = 50; // percentage
  let isDragging = false;
  let containerElement: HTMLDivElement;

  // Error panel state
  let errorPanelExpanded = false;
  let errorHideTimeout: number | null = null;

  // Show errors when they appear, then auto-collapse after 5 seconds
  $: {
    if (validationErrors.length > 0) {
      errorPanelExpanded = true;

      // Clear any existing timeout
      if (errorHideTimeout) {
        clearTimeout(errorHideTimeout);
      }

      // Auto-collapse after 5 seconds
      errorHideTimeout = setTimeout(() => {
        errorPanelExpanded = false;
      }, 5000);
    } else {
      // Hide completely when errors are cleared (successful validation)
      errorPanelExpanded = false;
      if (errorHideTimeout) {
        clearTimeout(errorHideTimeout);
        errorHideTimeout = null;
      }
    }
  }

  // Auto-scroll to errors when they appear (separate reactive statement)
  $: if (errorPanelExpanded && errorPanelElement) {
    setTimeout(() => {
      errorPanelElement?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 100);
  }

  function toggleErrorPanel() {
    errorPanelExpanded = !errorPanelExpanded;
  }

  function handleDividerMouseDown(e: MouseEvent) {
    isDragging = true;
    e.preventDefault();
  }

  function handleMouseMove(e: MouseEvent) {
    if (!isDragging || !containerElement) return;

    const containerRect = containerElement.getBoundingClientRect();
    const newLeftWidth = ((e.clientX - containerRect.left) / containerRect.width) * 100;

    // Constrain between 20% and 80%
    leftPanelWidth = Math.min(Math.max(newLeftWidth, 20), 80);
  }

  function handleMouseUp() {
    isDragging = false;
    // Save to localStorage when dragging stops
    localStorage.setItem('agentmaestro-divider-position', leftPanelWidth.toString());
  }

  onMount(() => {
    environment.showNotification('AgentMaestro started', 'info');

    // Load saved divider position from localStorage
    const savedPosition = localStorage.getItem('agentmaestro-divider-position');
    if (savedPosition) {
      const position = parseFloat(savedPosition);
      if (!isNaN(position) && position >= 20 && position <= 80) {
        leftPanelWidth = position;
      }
    }


    // Add global mouse event listeners for resizing
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
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
        // Only update DAG when validation succeeds
        validatedWorkflow = currentWorkflow;
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
      <ThemeToggle on:themeChange={(e) => theme = e.detail} />
    </div>
  </header>
  <main class="flex-1 flex flex-col overflow-hidden">
    <!-- Top 2-column region with resizable divider -->
    <div bind:this={containerElement} class="flex-1 flex gap-0 p-4 min-h-0" class:dragging={isDragging}>
      <div style="width: {leftPanelWidth}%; min-width: 20%; max-width: 80%;" class="flex flex-col min-h-0">
        <Card className="min-h-0 card-elevated flex-1 flex flex-col">
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
            {#if validationErrors.length > 0}
              {#if errorPanelExpanded}
                <ErrorPanel bind:element={errorPanelElement} errors={validationErrors} {theme} visible={true} />
              {:else}
                <button
                  class="error-collapsed-indicator"
                  on:click={toggleErrorPanel}
                  class:dark={theme === 'dark'}
                >
                  <span class="error-icon">⚠️</span>
                  <span class="error-text">{validationErrors.length} validation {validationErrors.length === 1 ? 'error' : 'errors'}</span>
                  <span class="expand-icon">▼</span>
                </button>
              {/if}
            {/if}
          </CardContent>
        </Card>
      </div>

      <!-- Draggable divider -->
      <div
        class="divider"
        on:mousedown={handleDividerMouseDown}
        role="separator"
        aria-orientation="vertical"
      ></div>

      <div style="width: {100 - leftPanelWidth}%; min-width: 20%;" class="flex flex-col min-h-0">
        <Card className="min-h-0 card-elevated flex-1 flex flex-col">
          <CardHeader className="py-3">
            <h2 class="section-heading">DAG Visualization</h2>
          </CardHeader>
          <CardContent>
            <DAGVisualization workflow={validatedWorkflow} {isValid} {theme} />
          </CardContent>
        </Card>
      </div>
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

<style>
  .divider {
    width: 8px;
    cursor: col-resize;
    background: transparent;
    position: relative;
    user-select: none;
    margin: 0 4px;
  }

  .divider:hover::after,
  .dragging .divider::after {
    content: '';
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 2px;
    height: 100%;
    background: hsl(var(--primary));
    opacity: 0.5;
  }

  .divider:hover::after {
    opacity: 0.7;
  }

  .dragging {
    cursor: col-resize;
    user-select: none;
  }

  .error-collapsed-indicator {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid #ef4444;
    border-radius: 0.25rem;
    background-color: #fee2e2;
    color: #991b1b;
    cursor: pointer;
    transition: background-color 0.2s, border-color 0.2s;
    font-size: 0.875rem;
    font-weight: 500;
    width: 100%;
  }

  .error-collapsed-indicator:hover {
    background-color: #fecaca;
    border-color: #dc2626;
  }

  .error-collapsed-indicator.dark {
    background-color: #3f1515;
    border-color: #7f1d1d;
    color: #fca5a5;
  }

  .error-collapsed-indicator.dark:hover {
    background-color: #4f1d1d;
    border-color: #991b1b;
  }

  .error-collapsed-indicator .error-icon {
    font-size: 1rem;
  }

  .error-collapsed-indicator .error-text {
    flex: 1;
  }

  .error-collapsed-indicator .expand-icon {
    font-size: 0.75rem;
    opacity: 0.7;
  }
</style>
