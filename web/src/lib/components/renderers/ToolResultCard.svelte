<script lang="ts">
  import type { NormalizedToolResult } from '../../normalize';

  interface Props {
    data: NormalizedToolResult;
  }

  let { data }: Props = $props();

  let isError = $derived(data.isError === true);

  function formatContent(content: string | unknown[] | undefined): string {
    if (content == null) return '';
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content.map(block => {
        if (typeof block === 'object' && block !== null && 'text' in (block as Record<string, unknown>)) {
          return (block as Record<string, unknown>).text as string;
        }
        try {
          return JSON.stringify(block, null, 2);
        } catch {
          return String(block);
        }
      }).join('\n');
    }
    return String(content);
  }

  let text = $derived(formatContent(data.content));
  let truncated = $derived(text.length > 500);
</script>

<div class="result-card" class:result-error={isError}>
  <div class="result-header">
    <span class="result-label">{isError ? 'Error' : 'Result'}</span>
    {#if data.toolCallId}
      <span class="result-ref">{data.toolCallId}</span>
    {/if}
  </div>
  {#if text}
    <details class="result-details" open={!truncated}>
      <summary class="result-summary">{text.length > 80 ? text.slice(0, 80) + '\u2026' : text}</summary>
      <pre class="result-text">{text}</pre>
    </details>
  {/if}
</div>

<style>
  .result-card {
    padding: 0.375rem 0.75rem;
    border-radius: 0.5rem;
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.1);
    max-width: 85%;
    font-size: 0.75rem;
  }

  .result-error {
    border-color: hsl(var(--status-danger) / 0.3);
    background: hsl(var(--status-danger) / 0.04);
  }

  .result-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
  }

  .result-label {
    font-size: 0.6875rem;
    font-weight: 600;
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }

  .result-error .result-label {
    color: hsl(var(--status-danger));
  }

  .result-ref {
    font-size: 0.625rem;
    font-family: var(--font-mono);
    color: hsl(var(--muted-foreground));
    opacity: 0.7;
  }

  .result-details {
    margin-top: 0.25rem;
  }

  .result-summary {
    cursor: pointer;
    color: hsl(var(--muted-foreground));
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    padding: 0.125rem 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .result-summary:hover {
    color: hsl(var(--foreground));
  }

  .result-text {
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

  .result-error .result-text {
    color: hsl(var(--status-danger));
  }
</style>
