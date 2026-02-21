<script lang="ts">
  import type { ToolCallActivityData, ToolCallUpdateData } from '../../types';

  interface Props {
    data: ToolCallActivityData | ToolCallUpdateData;
    compact?: boolean;
  }

  let { data, compact = false }: Props = $props();

  let title = $derived(data.title ?? 'Unknown tool');
  let status = $derived(data.status);
</script>

{#if compact}
  <span class="compact-tool">
    <span class="tool-icon">{'\u2699'}</span>
    <span class="tool-label">{title}</span>
    {#if status}
      <span class="tool-status" class:status-completed={status === 'completed'} class:status-failed={status === 'failed'}>{status}</span>
    {/if}
  </span>
{:else}
  <div class="tool-card" class:tool-result={status === 'completed'}>
    <div class="tool-header">
      <span class="tool-icon">{'\u2699'}</span>
      <span class="tool-title">{title}</span>
      {#if status}
        <span class="status-badge" class:status-completed={status === 'completed'} class:status-failed={status === 'failed'} class:status-running={status === 'running'}>{status}</span>
      {/if}
    </div>
    {#if data.toolCallId}
      <div class="tool-id">{data.toolCallId}</div>
    {/if}
  </div>
{/if}

<style>
  .compact-tool {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
  }

  .tool-icon {
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .tool-label {
    color: hsl(var(--foreground));
  }

  .tool-status {
    font-size: 0.625rem;
    font-weight: 500;
    padding: 0.0625rem 0.25rem;
    border-radius: 0.1875rem;
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
  }

  .tool-card {
    padding: 0.5rem 0.75rem;
    border-radius: 0.5rem;
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.15);
    max-width: 85%;
  }

  .tool-result {
    border-color: hsl(var(--status-success) / 0.3);
    background: hsl(var(--status-success) / 0.04);
  }

  .tool-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.8125rem;
  }

  .tool-title {
    font-weight: 500;
    color: hsl(var(--foreground));
  }

  .status-badge {
    font-size: 0.625rem;
    font-weight: 600;
    padding: 0.0625rem 0.375rem;
    border-radius: 0.25rem;
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .status-completed {
    background: hsl(var(--status-success) / 0.15);
    color: hsl(var(--status-success));
  }

  .status-failed {
    background: hsl(var(--status-danger) / 0.15);
    color: hsl(var(--status-danger));
  }

  .status-running {
    background: hsl(var(--status-working) / 0.15);
    color: hsl(var(--status-working));
  }

  .tool-id {
    font-size: 0.625rem;
    font-family: var(--font-mono);
    color: hsl(var(--muted-foreground));
    margin-top: 0.1875rem;
    opacity: 0.7;
  }
</style>
