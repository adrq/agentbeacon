<script lang="ts">
  export let isExecuting: boolean = false;

  // Mock execution logs for demo
  let logs: Array<{
    timestamp: string;
    level: 'info' | 'warning' | 'error' | 'success';
    node?: string;
    message: string;
  }> = [
    {
      timestamp: new Date().toLocaleTimeString(),
      level: 'info',
      message: 'AgentMaestro initialized successfully'
    }
  ];

  let outputElement: HTMLDivElement;

  // Simulate execution logs
  $: if (isExecuting) {
    simulateExecution();
  }

  function simulateExecution() {
    const executionLogs = [
      { level: 'info' as const, message: 'Starting workflow execution...' },
      { level: 'info' as const, node: 'analyze', message: 'Node analyze: Starting execution' },
      { level: 'info' as const, node: 'analyze', message: 'Connecting to claude-code agent...' },
      { level: 'info' as const, node: 'analyze', message: 'Sending prompt to agent...' },
    ];

    let index = 0;
    const interval = setInterval(() => {
      if (index < executionLogs.length && isExecuting) {
        const log = executionLogs[index];
        addLog(log.level, log.message, log.node);
        index++;
      } else {
        clearInterval(interval);
        if (isExecuting) {
          addLog('success', 'Workflow execution completed successfully');
        }
      }
    }, 1000);
  }

  function addLog(
    level: 'info' | 'warning' | 'error' | 'success',
    message: string,
    node?: string
  ) {
    logs = [...logs, {
      timestamp: new Date().toLocaleTimeString(),
      level,
      node,
      message
    }];

    // Auto-scroll to bottom
    setTimeout(() => {
      if (outputElement) {
        outputElement.scrollTop = outputElement.scrollHeight;
      }
    }, 0);
  }

  function clearLogs() {
    logs = [{
      timestamp: new Date().toLocaleTimeString(),
      level: 'info',
      message: 'Logs cleared'
    }];
  }

  function getLevelIcon(level: string): string {
    switch (level) {
      case 'info': return 'ℹ️';
      case 'warning': return '⚠️';
      case 'error': return '❌';
      case 'success': return '✅';
      default: return '📝';
    }
  }

  function getLevelClass(level: string): string {
    return `log-${level}`;
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

      <button class="clear-btn" on:click={clearLogs}>
        🗑️ Clear
      </button>
    </div>
  </div>

  <div
    class="output-content"
    bind:this={outputElement}
  >
    {#each logs as log}
      <div class="log-entry {getLevelClass(log.level)}">
        <span class="log-timestamp">{log.timestamp}</span>
        <span class="log-level-icon">{getLevelIcon(log.level)}</span>
        {#if log.node}
          <span class="log-node">[{log.node}]</span>
        {/if}
        <span class="log-message">{log.message}</span>
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

  .clear-btn {
    padding: 0.25rem 0.5rem;
    background: #374151;
    color: #d1d5db;
    border: 1px solid #4b5563;
    border-radius: 0.25rem;
    font-size: 0.75rem;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }

  .clear-btn:hover {
    background: #4b5563;
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

  .log-timestamp {
    color: #6b7280;
    font-size: 0.65rem;
    min-width: 80px;
    flex-shrink: 0;
  }

  .log-level-icon {
    min-width: 20px;
    flex-shrink: 0;
  }

  .log-node {
    color: #8b5cf6;
    font-weight: 600;
    min-width: 60px;
    flex-shrink: 0;
  }

  .log-message {
    color: #e5e7eb;
    flex: 1;
  }

  .log-info .log-message {
    color: #e5e7eb;
  }

  .log-warning .log-message {
    color: #fbbf24;
  }

  .log-error .log-message {
    color: #f87171;
  }

  .log-success .log-message {
    color: #34d399;
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
