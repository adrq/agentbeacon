<script lang="ts">
  import type { RunStatus } from '../types';

  export let isExecuting: boolean = false;
  export let logs: string[] | undefined = undefined;
  export let status: RunStatus | undefined = undefined;

  let outputElement: HTMLDivElement;

  // Use provided logs or default placeholder messages
  $: displayLogs = logs || [
    '[12:34:56] ℹ️ AgentMaestro initialized successfully',
    '[12:34:57] ℹ️ Waiting for workflow execution...',
  ];

  // Auto-scroll to bottom when logs change
  $: if (outputElement && displayLogs) {
    setTimeout(() => {
      if (outputElement) {
        outputElement.scrollTop = outputElement.scrollHeight;
      }
    }, 0);
  }
</script>

<div class="output-panel">
  <div class="output-header">
    <h3>Execution Output</h3>
    <div class="header-controls">
      {#if isExecuting}
        <div class="status-indicator">
          <div class="pulse-dot"></div>
          Executing...
        </div>
      {:else if status === 'completed'}
        <div class="status-indicator completed">
          ✅ Completed
        </div>
      {:else if status === 'failed'}
        <div class="status-indicator failed">
          ❌ Failed
        </div>
      {:else if status === 'canceled'}
        <div class="status-indicator canceled">
          🚫 Canceled
        </div>
      {:else}
        <div class="status-indicator idle">
          ⏸️ Idle
        </div>
      {/if}
    </div>
  </div>

  <div
    class="output-content"
    bind:this={outputElement}
  >
    {#each displayLogs as log}
      <div class="log-entry">
        <span class="log-message">{log}</span>
      </div>
    {/each}
  </div>
</div>

<style>
  .output-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
    background: #1f2937;
    color: #e5e7eb;
  }

  :global(.light) .output-panel {
    background: #f9fafb;
    color: #1f2937;
  }

  .output-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1rem;
    border-bottom: 1px solid #374151;
    background: #1f2937;
  }

  :global(.light) .output-header {
    background: #f3f4f6;
    border-bottom-color: #e5e7eb;
  }

  .output-header h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
    color: #f9fafb;
  }

  :global(.light) .output-header h3 {
    color: #1f2937;
  }

  .header-controls {
    display: flex;
    align-items: center;
    gap: 1rem;
  }

  .status-indicator {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.75rem;
    color: #10b981;
  }

  .status-indicator.completed {
    color: #10b981;
  }

  .status-indicator.failed {
    color: #ef4444;
  }

  .status-indicator.canceled {
    color: #6b7280;
  }

  .status-indicator.idle {
    color: #6b7280;
  }

  .pulse-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #10b981;
    animation: pulse 1.5s ease-in-out infinite;
  }

  .output-content {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 0.75rem;
    line-height: 1.5;
    background: #111827;
  }

  :global(.light) .output-content {
    background: #ffffff;
  }

  .log-entry {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
    padding: 0.25rem 0;
  }

  .log-message {
    color: #e5e7eb;
    flex: 1;
    word-wrap: break-word;
  }

  :global(.light) .log-message {
    color: #374151;
  }

  @keyframes pulse {
    0%, 100% {
      opacity: 1;
    }
    50% {
      opacity: 0.5;
    }
  }

  /* Scrollbar styling for webkit browsers */
  .output-content::-webkit-scrollbar {
    width: 6px;
  }

  .output-content::-webkit-scrollbar-track {
    background: #1f2937;
  }

  :global(.light) .output-content::-webkit-scrollbar-track {
    background: #f3f4f6;
  }

  .output-content::-webkit-scrollbar-thumb {
    background: #4b5563;
    border-radius: 3px;
  }

  :global(.light) .output-content::-webkit-scrollbar-thumb {
    background: #d1d5db;
  }

  .output-content::-webkit-scrollbar-thumb:hover {
    background: #6b7280;
  }

  :global(.light) .output-content::-webkit-scrollbar-thumb:hover {
    background: #9ca3af;
  }
</style>
