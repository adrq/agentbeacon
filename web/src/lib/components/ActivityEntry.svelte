<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { ActivityEntry, Theme } from '../types';

  export let entry: ActivityEntry;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{
    viewDetails: void;
    viewResults: void;
    debug: void;
    stop: void;
    rerun: void;
  }>();

  function getStatusIcon(status: string): string {
    switch (status) {
      case 'pending': return '⏸️';
      case 'running': return '🔄';
      case 'completed': return '✅';
      case 'failed': return '❌';
      case 'canceled': return '🚫';
      default: return '📝';
    }
  }
</script>

<div class="activity-entry" class:dark={theme === 'dark'}>
  <div class="entry-header">
    <span class="status-icon">{getStatusIcon(entry.status)}</span>
    <div class="entry-info">
      <h4>{entry.workflowName} - Run #{entry.runNumber}</h4>
      <div class="entry-meta">
        <span>{entry.startedAt}</span>
        <span>•</span>
        <span>{entry.version}</span>
        {#if entry.duration}
          <span>•</span>
          <span>{entry.duration}</span>
        {/if}
      </div>
    </div>
  </div>

  {#if entry.status === 'running' && entry.progress !== undefined}
    <div class="progress-bar">
      <div class="progress-fill" style="width: {entry.progress}%"></div>
    </div>
  {/if}

  {#if entry.output}
    <p class="entry-output">{entry.output}</p>
  {/if}

  {#if entry.error}
    <p class="entry-error">{entry.error}</p>
  {/if}

  <div class="entry-actions">
    {#if entry.status === 'pending'}
      <button class="btn-action" on:click={() => dispatch('viewDetails')}>View Details</button>
      <button class="btn-action" on:click={() => dispatch('stop')}>Cancel</button>
    {:else if entry.status === 'running'}
      <button class="btn-action" on:click={() => dispatch('viewDetails')}>View Details</button>
      <button class="btn-action" on:click={() => dispatch('stop')}>Stop</button>
    {:else if entry.status === 'completed'}
      <button class="btn-action" on:click={() => dispatch('viewResults')}>View Results</button>
      <button class="btn-action" on:click={() => dispatch('rerun')}>Rerun</button>
    {:else if entry.status === 'failed' || entry.status === 'canceled'}
      <button class="btn-action" on:click={() => dispatch('debug')}>Debug</button>
      <button class="btn-action" on:click={() => dispatch('rerun')}>Rerun</button>
    {/if}
  </div>
</div>

<style>
  .activity-entry {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    padding: 1rem;
    margin-bottom: 0.75rem;
  }

  .activity-entry.dark {
    background: #1e293b;
    border-color: #334155;
  }

  .entry-header {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
  }

  .status-icon {
    font-size: 1.5rem;
    flex-shrink: 0;
  }

  .entry-info {
    flex: 1;
    min-width: 0;
  }

  .entry-info h4 {
    margin: 0 0 0.25rem 0;
    font-size: 0.9375rem;
    font-weight: 600;
    color: #0f172a;
  }

  .dark .entry-info h4 {
    color: #e2e8f0;
  }

  .entry-meta {
    font-size: 0.8125rem;
    color: #64748b;
    display: flex;
    gap: 0.5rem;
  }

  .dark .entry-meta {
    color: #94a3b8;
  }

  .progress-bar {
    height: 4px;
    background: #e2e8f0;
    border-radius: 2px;
    margin-bottom: 0.75rem;
    overflow: hidden;
  }

  .dark .progress-bar {
    background: #334155;
  }

  .progress-fill {
    height: 100%;
    background: #3b82f6;
    transition: width 0.3s ease;
  }

  .entry-output,
  .entry-error {
    margin: 0 0 0.75rem 0;
    font-size: 0.875rem;
    line-height: 1.5;
  }

  .entry-output {
    color: #059669;
  }

  .entry-error {
    color: #ef4444;
  }

  .entry-actions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .btn-action {
    padding: 0.375rem 0.75rem;
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
    border-radius: 0.25rem;
    font-size: 0.8125rem;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .btn-action:hover {
    background: #e2e8f0;
    border-color: #94a3b8;
  }

  .dark .btn-action {
    background: #334155;
    color: #cbd5e1;
    border-color: #475569;
  }

  .dark .btn-action:hover {
    background: #475569;
    border-color: #64748b;
  }
</style>
