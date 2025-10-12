<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { RunEntry, Theme } from '../types';

  export let run: RunEntry;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{
    viewDetails: void;
    stop: void;
    rerun: void;
    compare: void;
    debug: void;
  }>();

  function getStatusIcon(status: string): string {
    return status === 'running' ? '🔄' :
           status === 'success' ? '✅' :
           status === 'failed' ? '❌' : '📝';
  }
</script>

<div class="run-entry" class:dark={theme === 'dark'}>
  <div class="run-header">
    <div class="run-number">
      <span class="status-icon">{getStatusIcon(run.status)}</span>
      <span class="number">Run #{run.runNumber}</span>
    </div>
    <div class="run-meta">
      <span>{run.version}</span>
      <span>•</span>
      <span>{run.startedAt}</span>
      {#if run.duration}
        <span>•</span>
        <span>{run.duration}</span>
      {/if}
    </div>
  </div>

  <div class="run-input">
    <span class="label">Input:</span>
    <span class="value">{run.input}</span>
  </div>

  {#if run.nodeProgress}
    <div class="node-progress">
      {#each run.nodeProgress.completed as node}
        <span class="node-badge completed">✅ {node}</span>
      {/each}
      {#each run.nodeProgress.running as node}
        <span class="node-badge running">🔄 {node}</span>
      {/each}
      {#each run.nodeProgress.waiting as node}
        <span class="node-badge waiting">⏸️ {node}</span>
      {/each}
    </div>
  {/if}

  {#if run.output}
    <div class="run-output">{run.output}</div>
  {/if}

  {#if run.error}
    <div class="run-error">{run.error}</div>
  {/if}

  <div class="run-actions">
    <button class="btn-action" on:click={() => dispatch('viewDetails')}>View Details</button>
    {#if run.status === 'running'}
      <button class="btn-action" on:click={() => dispatch('stop')}>Stop</button>
    {/if}
    <button class="btn-action" on:click={() => dispatch('rerun')}>Rerun</button>
    <button class="btn-action" on:click={() => dispatch('compare')}>Compare</button>
    {#if run.status === 'failed'}
      <button class="btn-action" on:click={() => dispatch('debug')}>Debug</button>
    {/if}
  </div>
</div>

<style>
  .run-entry {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    padding: 1rem;
    margin-bottom: 0.75rem;
  }

  .run-entry.dark {
    background: #1e293b;
    border-color: #334155;
  }

  .run-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.75rem;
  }

  .run-number {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 600;
    color: #0f172a;
  }

  .dark .run-number {
    color: #e2e8f0;
  }

  .status-icon {
    font-size: 1.25rem;
  }

  .run-meta {
    font-size: 0.8125rem;
    color: #64748b;
    display: flex;
    gap: 0.5rem;
  }

  .dark .run-meta {
    color: #94a3b8;
  }

  .run-input {
    margin-bottom: 0.75rem;
    font-size: 0.875rem;
  }

  .label {
    font-weight: 600;
    color: #64748b;
  }

  .dark .label {
    color: #94a3b8;
  }

  .value {
    color: #0f172a;
  }

  .dark .value {
    color: #e2e8f0;
  }

  .node-progress {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }

  .node-badge {
    padding: 0.25rem 0.625rem;
    border-radius: 0.25rem;
    font-size: 0.75rem;
    font-weight: 500;
  }

  .node-badge.completed {
    background: #d1fae5;
    color: #065f46;
  }

  .node-badge.running {
    background: #dbeafe;
    color: #1e40af;
  }

  .node-badge.waiting {
    background: #f3f4f6;
    color: #4b5563;
  }

  .dark .node-badge.completed {
    background: #064e3b;
    color: #6ee7b7;
  }

  .dark .node-badge.running {
    background: #1e3a8a;
    color: #93c5fd;
  }

  .dark .node-badge.waiting {
    background: #374151;
    color: #d1d5db;
  }

  .run-output {
    margin-bottom: 0.75rem;
    color: #059669;
    font-size: 0.875rem;
  }

  .run-error {
    margin-bottom: 0.75rem;
    color: #ef4444;
    font-size: 0.875rem;
  }

  .run-actions {
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
