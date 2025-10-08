<script lang="ts">
  export let errors: Array<{
    type: 'syntax' | 'structural' | 'semantic';
    message: string;
    line?: number;
    node?: string;
    nodes?: string[];
  }> = [];
  export let theme: 'dark' | 'light' = 'dark';
  export let visible: boolean = true;
  export let onDismiss: (() => void) | null = null;
  export let element: HTMLDivElement | null = null;

  function getErrorBadgeColor(type: string): string {
    switch (type) {
      case 'syntax':
        return 'bg-red-600';
      case 'structural':
        return 'bg-orange-600';
      case 'semantic':
        return 'bg-yellow-600';
      default:
        return 'bg-gray-600';
    }
  }
</script>

{#if visible}
  <div bind:this={element} class="error-panel" class:dark={theme === 'dark'}>
    <div class="error-header">
      <div class="flex items-center gap-2">
        <span class="error-icon">⚠️</span>
        <h4 class="error-title">Validation Errors</h4>
      </div>
      {#if onDismiss}
        <button class="dismiss-button" on:click={onDismiss} aria-label="Dismiss errors">
          ✕
        </button>
      {/if}
    </div>

    <div class="error-list">
      {#each errors as error}
        <div class="error-item">
          <span class="error-badge {getErrorBadgeColor(error.type)}">
            {error.type}
          </span>
          <div class="error-content">
            <p class="error-message">{error.message}</p>
            <div class="error-metadata">
              {#if error.line !== undefined}
                <span class="metadata-item">Line {error.line}</span>
              {/if}
              {#if error.node}
                <span class="metadata-item">Node: {error.node}</span>
              {/if}
              {#if error.nodes && error.nodes.length > 0}
                <span class="metadata-item">Nodes: {error.nodes.join(', ')}</span>
              {/if}
            </div>
          </div>
        </div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .error-panel {
    border: 1px solid #ef4444;
    border-radius: 0.25rem;
    background-color: #fee2e2;
    color: #991b1b;
    padding: 0.5rem;
    margin-top: 0.5rem;
  }

  .error-panel.dark {
    background-color: #3f1515;
    border-color: #7f1d1d;
    color: #fca5a5;
  }

  .error-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
  }

  .error-icon {
    font-size: 1rem;
  }

  .error-title {
    font-size: 0.75rem;
    font-weight: 600;
    margin: 0;
  }

  .dismiss-button {
    background: transparent;
    border: none;
    color: inherit;
    cursor: pointer;
    font-size: 1.25rem;
    padding: 0.25rem;
    line-height: 1;
    opacity: 0.7;
    transition: opacity 0.2s;
  }

  .dismiss-button:hover {
    opacity: 1;
  }

  .error-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .error-item {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.5rem;
    background-color: rgba(255, 255, 255, 0.5);
    border-radius: 0.25rem;
  }

  .error-panel.dark .error-item {
    background-color: rgba(0, 0, 0, 0.2);
  }

  .error-badge {
    display: inline-block;
    padding: 0.25rem 0.5rem;
    border-radius: 0.25rem;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    color: white;
    flex-shrink: 0;
  }

  .error-content {
    flex: 1;
    min-width: 0;
  }

  .error-message {
    margin: 0 0 0.25rem 0;
    font-size: 0.875rem;
    font-weight: 500;
  }

  .error-metadata {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }

  .metadata-item {
    font-size: 0.75rem;
    opacity: 0.8;
  }

  /* Light theme overrides */
  .error-panel:not(.dark) .error-message {
    color: #7f1d1d;
  }

  .error-panel:not(.dark) .metadata-item {
    color: #991b1b;
  }
</style>
