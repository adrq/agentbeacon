<script lang="ts">
  interface Props {
    message: string;
    stderr?: string;
  }

  let { message, stderr }: Props = $props();
</script>

<div class="error-panel">
  <div class="error-header">
    <span class="error-icon">{'\u2716'}</span>
    <span class="error-label">Error</span>
  </div>
  <div class="error-message">{message}</div>
  {#if stderr}
    <pre class="error-stderr">{stderr}</pre>
  {/if}
</div>

<style>
  .error-panel {
    border-radius: 0.5rem;
    border: 1px solid hsl(var(--status-danger) / 0.3);
    background: hsl(var(--status-danger) / 0.08);
    padding: 0.5rem 0.75rem;
    max-width: 85%;
  }

  .error-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    margin-bottom: 0.25rem;
  }

  .error-icon {
    font-size: 0.75rem;
    color: hsl(var(--status-danger));
  }

  .error-label {
    font-size: 0.8125rem;
    font-weight: 600;
    color: hsl(var(--status-danger));
  }

  .error-message {
    font-size: 0.8125rem;
    line-height: 1.5;
    color: hsl(var(--foreground));
    word-break: break-word;
  }

  .error-stderr {
    margin-top: 0.375rem;
    padding: 0.5rem;
    border-radius: 0.375rem;
    background: hsl(var(--muted) / 0.3);
    font-family: var(--font-mono);
    font-size: 0.75rem;
    line-height: 1.4;
    color: hsl(var(--foreground));
    overflow-x: auto;
    max-height: 20rem;
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
