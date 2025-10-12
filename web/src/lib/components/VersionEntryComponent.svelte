<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { VersionEntry, Theme } from '../types';

  export let version: VersionEntry;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{ view: void; run: void }>();
</script>

<div class="version-entry" class:dark={theme === 'dark'} class:current={version.isCurrent}>
  <div class="version-header">
    <div class="version-title">
      {#if version.isCurrent}<span class="current-indicator">●</span>{/if}
      <h4>{version.version}</h4>
    </div>
    <span class="version-time">{version.timestamp}</span>
  </div>

  <p class="commit-message">"{version.commitMessage}"</p>

  <div class="run-stats">
    <span>{version.runStats.total} runs</span>
    <span>•</span>
    {#if version.runStats.success > 0}
      <span class="stat-success">{version.runStats.success} success</span>
    {/if}
    {#if version.runStats.failed > 0}
      <span class="stat-failed">{version.runStats.failed} failed</span>
    {/if}
    {#if version.runStats.running > 0}
      <span class="stat-running">{version.runStats.running} running</span>
    {/if}
  </div>

  <div class="version-actions">
    <button class="btn-action" on:click={() => dispatch('view')}>View</button>
    <button class="btn-action" on:click={() => dispatch('run')}>Run</button>
  </div>
</div>

<style>
  .version-entry {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    padding: 1rem;
    margin-bottom: 0.75rem;
  }

  .version-entry.dark {
    background: #1e293b;
    border-color: #334155;
  }

  .version-entry.current {
    border-color: #3b82f6;
    background: #eff6ff;
  }

  .version-entry.dark.current {
    border-color: #60a5fa;
    background: #1e3a8a;
  }

  .version-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
  }

  .version-title {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .current-indicator {
    color: #3b82f6;
    font-size: 0.75rem;
  }

  .version-title h4 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
    color: #0f172a;
  }

  .dark .version-title h4 {
    color: #e2e8f0;
  }

  .version-time {
    font-size: 0.8125rem;
    color: #64748b;
  }

  .dark .version-time {
    color: #94a3b8;
  }

  .commit-message {
    margin: 0 0 0.75rem 0;
    font-size: 0.875rem;
    color: #475569;
    font-style: italic;
  }

  .dark .commit-message {
    color: #cbd5e1;
  }

  .run-stats {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    font-size: 0.8125rem;
    color: #64748b;
    margin-bottom: 0.75rem;
  }

  .dark .run-stats {
    color: #94a3b8;
  }

  .stat-success {
    color: #059669;
  }

  .stat-failed {
    color: #ef4444;
  }

  .stat-running {
    color: #3b82f6;
  }

  .version-actions {
    display: flex;
    gap: 0.5rem;
  }

  .btn-action {
    padding: 0.375rem 0.875rem;
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
