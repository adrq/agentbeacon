<script lang="ts">
  import type { NormalizedToolCall } from '../../normalize';

  interface Props {
    data: NormalizedToolCall;
    compact?: boolean;
  }

  let { data, compact = false }: Props = $props();

  let title = $derived(data.title || 'Unknown tool');
  let status = $derived(data.status);
  let hasInput = $derived(data.input != null);
  let hasContent = $derived(Array.isArray(data.content) && data.content.length > 0);

  function formatInput(input: unknown): string {
    if (typeof input === 'string') return input;
    try {
      return JSON.stringify(input, null, 2);
    } catch {
      return String(input);
    }
  }

  function inputSummary(input: unknown): string {
    if (typeof input === 'object' && input !== null) {
      const entries = Object.entries(input as Record<string, unknown>);
      if (entries.length === 0) return '{}';
      const [key, val] = entries[0];
      const valStr = typeof val === 'string'
        ? (val.length > 60 ? val.slice(0, 60) + '\u2026' : val)
        : JSON.stringify(val)?.slice(0, 60) ?? '';
      const suffix = entries.length > 1 ? ` + ${entries.length - 1} more` : '';
      return `${key}: ${valStr}${suffix}`;
    }
    return String(input).slice(0, 80);
  }

  function contentSummary(content: unknown[]): string {
    const first = content[0];
    if (typeof first === 'object' && first !== null && 'text' in (first as Record<string, unknown>)) {
      const text = (first as Record<string, unknown>).text as string;
      return text.length > 80 ? text.slice(0, 80) + '\u2026' : text;
    }
    return `${content.length} block${content.length !== 1 ? 's' : ''}`;
  }
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
  <div class="tool-card" class:tool-completed={status === 'completed'}>
    <div class="tool-header">
      <span class="tool-icon">{'\u2699'}</span>
      <span class="tool-title">{title}</span>
      {#if status}
        <span class="status-badge" class:status-completed={status === 'completed'} class:status-failed={status === 'failed'} class:status-running={status === 'running'}>{status}</span>
      {/if}
    </div>
    {#if hasInput}
      <details class="tool-details">
        <summary class="tool-summary">{inputSummary(data.input)}</summary>
        <pre class="tool-json">{formatInput(data.input)}</pre>
      </details>
    {:else if hasContent}
      <details class="tool-details">
        <summary class="tool-summary">{contentSummary(data.content!)}</summary>
        <pre class="tool-json">{formatInput(data.content)}</pre>
      </details>
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

  .tool-completed {
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

  .tool-details {
    margin-top: 0.375rem;
    font-size: 0.75rem;
  }

  .tool-summary {
    cursor: pointer;
    color: hsl(var(--muted-foreground));
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    padding: 0.125rem 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-summary:hover {
    color: hsl(var(--foreground));
  }

  .tool-json {
    margin-top: 0.25rem;
    padding: 0.375rem 0.5rem;
    border-radius: 0.25rem;
    background: hsl(var(--muted) / 0.3);
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    line-height: 1.4;
    color: hsl(var(--foreground));
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 20rem;
    overflow-y: auto;
  }
</style>
