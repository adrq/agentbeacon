<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { WorkflowCard, Theme } from '../types';

  export let workflow: WorkflowCard;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{ open: void; run: void }>();
</script>

<div class="workflow-card" class:dark={theme === 'dark'}>
  <div class="card-header">
    <div class="card-title">
      {#if workflow.pinned}<span class="pin-icon">📌</span>{/if}
      <h3>{workflow.name}</h3>
    </div>
    <span class="status-indicator" class:running={workflow.status === 'running'}>
      {workflow.status === 'running' ? '●' : '○'}
    </span>
  </div>

  <div class="card-body">
    <div class="stat-row">
      <span class="stat-label">Running:</span>
      <span class="stat-value">{workflow.runStats.runningCount}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Completed today:</span>
      <span class="stat-value">{workflow.runStats.completedToday}</span>
    </div>
    <div class="stat-row">
      <span class="stat-label">Version:</span>
      <span class="stat-value">{workflow.version}</span>
    </div>
    {#if workflow.lastStatus === 'failed' && workflow.lastFailedTime}
      <div class="last-failed">
        <span class="error-text">Last failed {workflow.lastFailedTime}</span>
      </div>
    {/if}
  </div>

  <div class="card-actions">
    <button class="btn-open" on:click={() => dispatch('open')}>Open</button>
    <button class="btn-run" on:click={() => dispatch('run')}>Run▼</button>
  </div>
</div>

<style>
  .workflow-card {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    padding: 1rem;
    transition: all 0.2s ease;
    cursor: pointer;
  }

  .workflow-card:hover {
    border-color: #3b82f6;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  }

  .workflow-card.dark {
    background: #1e293b;
    border-color: #334155;
  }

  .workflow-card.dark:hover {
    border-color: #60a5fa;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.75rem;
  }

  .card-title {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex: 1;
    min-width: 0;
  }

  .card-title h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
    color: #0f172a;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .dark .card-title h3 {
    color: #e2e8f0;
  }

  .pin-icon {
    font-size: 0.875rem;
    flex-shrink: 0;
  }

  .status-indicator {
    font-size: 1.25rem;
    color: #94a3b8;
  }

  .status-indicator.running {
    color: #10b981;
  }

  .card-body {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin-bottom: 1rem;
    font-size: 0.875rem;
  }

  .stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .stat-label {
    color: #64748b;
  }

  .dark .stat-label {
    color: #94a3b8;
  }

  .stat-value {
    font-weight: 600;
    color: #0f172a;
  }

  .dark .stat-value {
    color: #e2e8f0;
  }

  .last-failed {
    margin-top: 0.25rem;
  }

  .error-text {
    color: #ef4444;
    font-size: 0.8125rem;
  }

  .card-actions {
    display: flex;
    gap: 0.5rem;
  }

  .btn-open,
  .btn-run {
    flex: 1;
    padding: 0.5rem 1rem;
    border: none;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .btn-open {
    background: #3b82f6;
    color: #ffffff;
  }

  .btn-open:hover {
    background: #2563eb;
  }

  .btn-run {
    background: #10b981;
    color: #ffffff;
  }

  .btn-run:hover {
    background: #059669;
  }

  @media (max-width: 768px) {
    .workflow-card {
      width: 100%;
    }
  }
</style>
