<script lang="ts">
  export let isExecuting: boolean = false;
  export let logs: string[] | undefined = undefined;

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
    background: var(--output-bg);
    color: #e5e7eb;
  }

  .output-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1rem;
    border-bottom: 1px solid #374151;
    background: #1f2937;
  }

  .output-header h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 600;
    color: #f9fafb;
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
    background: var(--output-bg);
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

  .output-content::-webkit-scrollbar-thumb {
    background: #4b5563;
    border-radius: 3px;
  }

  .output-content::-webkit-scrollbar-thumb:hover {
    background: #6b7280;
  }
</style>
