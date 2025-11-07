<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { createEventDispatcher } from 'svelte';
  import type { Theme, RouteParams, BreadcrumbSegment, NodeStatus } from '../lib/types';
  import type { ExecutionDetail, ExecutionEvent } from '../lib/api';
  import { api } from '../lib/api';
  import ScreenHeader from '../lib/components/ScreenHeader.svelte';
  import TabNavigation from '../lib/components/TabNavigation.svelte';
  import SplitPanel from '../lib/components/SplitPanel.svelte';
  import DAGVisualization from '../lib/components/DAGVisualization.svelte';
  import OutputPanel from '../lib/components/OutputPanel.svelte';
  import DiffViewer from '../lib/components/DiffViewer.svelte';

  export let theme: Theme;
  export let params: RouteParams;

  const dispatch = createEventDispatcher<{
    navigateToWorkflowEditor: { workflowId: string };
  }>();

  let activeTabIndex = 0;
  const tabs = ['🎯 Execution', '📝 Diff', '📋 Logs', '📦 Artifacts'];

  // Real execution data
  let execution: ExecutionDetail | null = null;
  let executionState: NodeStatus = {};
  let logMessages: string[] = [];
  let workflowYaml = '';
  let errorMessage = '';

  let pollingInterval: number | undefined;

  const breadcrumbs: BreadcrumbSegment[] = [
    { label: '🎭 Fix Frontend Tests', path: '/editor/workflow-demo' },
    { label: 'Run #24', path: '/run/run-demo' }
  ];

  async function fetchExecution() {
    if (!params.runId) return;

    try {
      const data = await api.getExecutionDetail(params.runId);
      execution = data;

      // Update execution state for DAG
      executionState = data.task_states || {};

      // Update logs from events
      logMessages = data.events.map(formatEvent);

      // Extract workflow YAML
      workflowYaml = data.workflow_definition || '';

      // Clear error on success
      if (errorMessage) {
        errorMessage = '';
      }

      // Stop polling if terminal state
      if (data.status === 'completed' || data.status === 'failed' || data.status === 'canceled') {
        stopPolling();
      }
    } catch (error) {
      errorMessage = error instanceof Error ? error.message : 'Failed to fetch execution';
      // Continue polling even on error
    }
  }

  function formatEvent(event: ExecutionEvent): string {
    const timestamp = new Date(event.timestamp).toLocaleTimeString();
    const taskInfo = event.task_id ? ` [${event.task_id}]` : '';
    return `[${timestamp}]${taskInfo} ${event.message}`;
  }

  function startPolling() {
    if (pollingInterval) return;
    pollingInterval = window.setInterval(fetchExecution, 2000);
  }

  function stopPolling() {
    if (pollingInterval) {
      clearInterval(pollingInterval);
      pollingInterval = undefined;
    }
  }

  onMount(async () => {
    await fetchExecution();

    // Start polling if execution is active
    if (execution && (execution.status === 'pending' || execution.status === 'running')) {
      startPolling();
    }
  });

  onDestroy(() => {
    stopPolling();
  });

  function formatTime(timestamp: string): string {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);

    if (hours > 0) return `${hours}h ago`;
    if (minutes > 0) return `${minutes}m ago`;
    return `${seconds}s ago`;
  }

  function formatDuration(startedAt: string, completedAt?: string): string {
    const start = new Date(startedAt);
    const end = completedAt ? new Date(completedAt) : new Date();
    const diff = end.getTime() - start.getTime();
    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);

    if (hours > 0) return `${hours}h ${minutes % 60}m`;
    if (minutes > 0) return `${minutes}m ${seconds % 60}s`;
    return `${seconds}s`;
  }

  // Placeholder diff content
  const beforeCode = `describe('Authentication', () => {
  it('should login successfully', () => {
    const user = { email: 'test@example.com', password: 'password' };
    const result = login(user);
    expect(result).toBe(true);
  });

  it('should reject invalid credentials', () => {
    const user = { email: 'test@example.com', password: 'wrong' };
    const result = login(user);
    expect(result).toBe(false);
  });
});`;

  const afterCode = `describe('Authentication', () => {
  it('should login successfully', async () => {
    const user = { email: 'test@example.com', password: 'password' };
    const result = await login(user);
    expect(result).toBe(true);
    expect(result.token).toBeDefined();
  });

  it('should reject invalid credentials', async () => {
    const user = { email: 'test@example.com', password: 'wrong' };
    await expect(login(user)).rejects.toThrow('Invalid credentials');
  });

  it('should handle missing email', async () => {
    const user = { email: '', password: 'password' };
    await expect(login(user)).rejects.toThrow('Email required');
  });
});`;

  function handleTabChange(event: CustomEvent<{ index: number; label: string }>) {
    activeTabIndex = event.detail.index;
  }

  function handleBackToWorkflow() {
    dispatch('navigateToWorkflowEditor', { workflowId: 'workflow-demo' });
  }

  function handleBreadcrumbNavigate(event: CustomEvent<{ path: string }>) {
    if (event.detail.path === '/editor/workflow-demo') {
      dispatch('navigateToWorkflowEditor', { workflowId: 'workflow-demo' });
    }
  }

  // Passive button handlers (do nothing)
  function handleStop() {
    console.log('Stop clicked (passive)');
  }

  function handleRerun() {
    console.log('Rerun clicked (passive)');
  }

  function handleExport() {
    console.log('Export clicked (passive)');
  }

  function handleAcceptAll() {
    console.log('Accept All clicked (passive)');
  }

  function handleRejectAll() {
    console.log('Reject All clicked (passive)');
  }

  function handleReviewEach() {
    console.log('Review Each clicked (passive)');
  }
</script>

<div class="run-details" class:dark={theme === 'dark'}>
  <ScreenHeader
    breadcrumbSegments={breadcrumbs}
    {theme}
    on:navigate={handleBreadcrumbNavigate}
  >
    <div slot="actions" class="header-actions">
      <button class="btn-action" on:click={handleBackToWorkflow}>
        ← Back to Workflow
      </button>
      <button class="btn-action btn-stop" on:click={handleStop}>
        ⏹️ Stop
      </button>
      <button class="btn-action" on:click={handleRerun}>
        🔄 Rerun
      </button>
      <button class="btn-action" on:click={handleExport}>
        📥 Export
      </button>
    </div>
  </ScreenHeader>

  {#if errorMessage}
    <div class="error-banner" data-testid="error-indicator">
      <span class="error-icon">⚠️</span>
      <span class="error-text">{errorMessage}</span>
    </div>
  {/if}

  {#if execution}
    <div class="status-bar">
      <div class="status-item status-badge">
        <span class="status-icon">
          {#if execution.status === 'running'}🔄
          {:else if execution.status === 'completed'}✅
          {:else if execution.status === 'failed'}❌
          {:else if execution.status === 'canceled'}⏹️
          {:else}⏸️{/if}
        </span>
        <span class="status-text">
          {execution.status.charAt(0).toUpperCase() + execution.status.slice(1)}
          {#if execution.started_at}
            ({formatDuration(execution.started_at, execution.completed_at)})
          {/if}
        </span>
      </div>
      {#if execution.started_at}
        <div class="status-item">
          <span class="label">Started:</span>
          <span class="value">{formatTime(execution.started_at)}</span>
        </div>
      {/if}
      {#if execution.version}
        <div class="status-item">
          <span class="label">Version:</span>
          <span class="value">{execution.version}</span>
        </div>
      {/if}
      {#if execution.agent_name}
        <div class="status-item">
          <span class="label">Agent:</span>
          <span class="value">{execution.agent_name}</span>
        </div>
      {/if}
    </div>
  {/if}

  <div class="details-content">
    <TabNavigation
      {tabs}
      {activeTabIndex}
      {theme}
      on:tabChange={handleTabChange}
    />

    <div class="tab-content">
      {#if activeTabIndex === 0}
        <!-- Execution Tab -->
        <div class="execution-tab">
          <SplitPanel storageKey="run-details-execution-split" initialLeftWidth={50}>
            <div slot="left" class="panel-content">
              <div class="dag-container">
                <h3 class="panel-title">DAG Progress</h3>
                <DAGVisualization
                  workflow={workflowYaml}
                  isValid={true}
                  {theme}
                  {executionState}
                  placeholderMode={false}
                />
              </div>
            </div>
            <div slot="right" class="panel-content">
              <div class="logs-container">
                <h3 class="panel-title">Live Logs</h3>
                <OutputPanel
                  isExecuting={execution?.status === 'pending' || execution?.status === 'running'}
                  status={execution?.status}
                  logs={logMessages}
                />
              </div>
            </div>
          </SplitPanel>
        </div>

      {:else if activeTabIndex === 1}
        <!-- Diff Tab -->
        <div class="diff-tab">
          <DiffViewer
            filePath="src/tests/auth.test.js"
            beforeCode={beforeCode}
            afterCode={afterCode}
            {theme}
            on:acceptAll={handleAcceptAll}
            on:rejectAll={handleRejectAll}
            on:reviewEach={handleReviewEach}
          />
        </div>

      {:else if activeTabIndex === 2}
        <!-- Logs Tab -->
        <div class="logs-tab">
          <OutputPanel
            isExecuting={execution?.status === 'pending' || execution?.status === 'running'}
            status={execution?.status}
            logs={logMessages}
          />
        </div>

      {:else if activeTabIndex === 3}
        <!-- Artifacts Tab -->
        <div class="artifacts-tab">
          <div class="placeholder-message">
            <span class="placeholder-icon">📦</span>
            <p class="placeholder-text">No artifacts generated yet</p>
            <p class="placeholder-hint">Artifacts will appear here when the workflow completes</p>
          </div>
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .run-details {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #f8fafc;
  }

  .run-details.dark {
    background: #0f172a;
  }

  .header-actions {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }

  .btn-action {
    padding: 0.5rem 0.875rem;
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .btn-action:hover {
    background: #e2e8f0;
    border-color: #94a3b8;
  }

  .btn-stop {
    background: #fef2f2;
    color: #991b1b;
    border-color: #fecaca;
  }

  .btn-stop:hover {
    background: #fee2e2;
    border-color: #fca5a5;
  }

  .run-details.dark .btn-action {
    background: #334155;
    color: #cbd5e1;
    border-color: #475569;
  }

  .run-details.dark .btn-action:hover {
    background: #475569;
    border-color: #64748b;
  }

  .run-details.dark .btn-stop {
    background: #7f1d1d;
    color: #fca5a5;
    border-color: #991b1b;
  }

  .run-details.dark .btn-stop:hover {
    background: #991b1b;
    border-color: #dc2626;
  }

  .error-banner {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.75rem 1.5rem;
    background: #fef2f2;
    border-bottom: 1px solid #fecaca;
    color: #991b1b;
  }

  .run-details.dark .error-banner {
    background: #7f1d1d;
    border-bottom-color: #991b1b;
    color: #fca5a5;
  }

  .error-icon {
    font-size: 1.25rem;
  }

  .error-text {
    flex: 1;
    font-size: 0.875rem;
    font-weight: 500;
  }

  .status-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 1.5rem;
    padding: 1rem 1.5rem;
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
  }

  .run-details.dark .status-bar {
    background: #1e293b;
    border-bottom-color: #334155;
  }

  .status-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.875rem;
  }

  .status-badge {
    padding: 0.375rem 0.75rem;
    background: #dbeafe;
    border-radius: 0.375rem;
    font-weight: 600;
  }

  .run-details.dark .status-badge {
    background: #1e3a8a;
    color: #93c5fd;
  }

  .status-icon {
    font-size: 1rem;
  }

  .status-text {
    color: #1e40af;
  }

  .run-details.dark .status-text {
    color: #93c5fd;
  }

  .label {
    font-weight: 600;
    color: #64748b;
  }

  .run-details.dark .label {
    color: #94a3b8;
  }

  .value {
    color: #0f172a;
  }

  .run-details.dark .value {
    color: #e2e8f0;
  }

  .details-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .tab-content {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .execution-tab,
  .diff-tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-content {
    height: 100%;
    overflow: auto;
    display: flex;
    flex-direction: column;
  }

  .dag-container,
  .logs-container {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .panel-title {
    margin: 0;
    padding: 0.75rem 1rem;
    background: #f1f5f9;
    border-bottom: 1px solid #e2e8f0;
    font-size: 0.875rem;
    font-weight: 600;
    color: #475569;
  }

  .run-details.dark .panel-title {
    background: #1e293b;
    border-bottom-color: #334155;
    color: #cbd5e1;
  }

  .logs-tab {
    flex: 1;
    overflow: auto;
  }

  .artifacts-tab {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 3rem;
  }

  .placeholder-message {
    text-align: center;
    max-width: 400px;
  }

  .placeholder-icon {
    font-size: 4rem;
    display: block;
    margin-bottom: 1rem;
  }

  .placeholder-text {
    margin: 0 0 0.5rem 0;
    font-size: 1.125rem;
    font-weight: 600;
    color: #0f172a;
  }

  .run-details.dark .placeholder-text {
    color: #e2e8f0;
  }

  .placeholder-hint {
    margin: 0;
    font-size: 0.875rem;
    color: #64748b;
  }

  .run-details.dark .placeholder-hint {
    color: #94a3b8;
  }

  @media (max-width: 768px) {
    .header-actions {
      gap: 0.25rem;
    }

    .btn-action {
      padding: 0.5rem;
      font-size: 0.75rem;
    }

    .status-bar {
      gap: 1rem;
      padding: 0.75rem 1rem;
    }

    .status-item {
      font-size: 0.8125rem;
    }
  }

  @media (max-width: 480px) {
    .btn-action {
      min-width: 2.5rem;
      padding: 0.5rem 0.25rem;
    }

    .status-bar {
      flex-direction: column;
      gap: 0.5rem;
    }

    .status-item {
      flex-wrap: wrap;
    }
  }
</style>
