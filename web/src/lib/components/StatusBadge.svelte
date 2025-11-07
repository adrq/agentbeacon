<script lang="ts">
  import type { RunStatus, Theme } from '../types';

  export let status: RunStatus;
  export let size: 'small' | 'medium' | 'large' = 'medium';
  export let theme: Theme;

  function getStatusIcon(status: RunStatus): string {
    return status === 'pending' ? '⏸️' :
           status === 'running' ? '🔄' :
           status === 'completed' ? '✅' :
           status === 'canceled' ? '🚫' :
           status === 'failed' ? '❌' : '📝';
  }

  function getStatusLabel(status: RunStatus): string {
    return status.charAt(0).toUpperCase() + status.slice(1);
  }
</script>

<div class="status-badge {size} {status}" class:dark={theme === 'dark'}>
  <span class="icon">{getStatusIcon(status)}</span>
  <span class="label">{getStatusLabel(status)}</span>
</div>

<style>
  .status-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.375rem 0.75rem;
    border-radius: 0.375rem;
    font-weight: 500;
  }

  .status-badge.small {
    padding: 0.25rem 0.5rem;
    font-size: 0.75rem;
  }

  .status-badge.small .icon {
    font-size: 0.875rem;
  }

  .status-badge.medium {
    padding: 0.375rem 0.75rem;
    font-size: 0.875rem;
  }

  .status-badge.medium .icon {
    font-size: 1rem;
  }

  .status-badge.large {
    padding: 0.5rem 1rem;
    font-size: 1rem;
  }

  .status-badge.large .icon {
    font-size: 1.25rem;
  }

  .status-badge.running {
    background: #dbeafe;
    color: #1e40af;
  }

  .status-badge.dark.running {
    background: #1e3a8a;
    color: #93c5fd;
  }

  .status-badge.pending {
    background: #e0e7ff;
    color: #4338ca;
  }

  .status-badge.dark.pending {
    background: #312e81;
    color: #a5b4fc;
  }

  .status-badge.completed {
    background: #d1fae5;
    color: #065f46;
  }

  .status-badge.dark.completed {
    background: #064e3b;
    color: #6ee7b7;
  }

  .status-badge.failed {
    background: #fee2e2;
    color: #991b1b;
  }

  .status-badge.dark.failed {
    background: #7f1d1d;
    color: #fca5a5;
  }

  .status-badge.canceled {
    background: #f1f5f9;
    color: #475569;
  }

  .status-badge.dark.canceled {
    background: #334155;
    color: #cbd5e1;
  }
</style>
