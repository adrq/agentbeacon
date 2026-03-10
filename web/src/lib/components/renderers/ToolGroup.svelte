<script lang="ts">
  import type { NormalizedToolCall, NormalizedToolResult } from '../../normalize';

  interface Props {
    call: NormalizedToolCall;
    result?: NormalizedToolResult;
  }

  let { call, result }: Props = $props();

  let title = $derived(call.title || 'Unknown tool');
  let displayStatus = $derived(
    result?.isError === true ? 'failed' :
    result != null ? 'completed' :
    call.status
  );
  let isError = $derived(result?.isError === true || displayStatus === 'failed');
  let isCompleted = $derived(displayStatus === 'completed');
  let isRunning = $derived(displayStatus === 'running');
  let hasDetails = $derived(call.input != null || (Array.isArray(call.content) && call.content.length > 0) || result?.content != null);

  let resultHint = $derived(result?.content ? formatContent(result.content).slice(0, 60) : '');

  function inputSummary(input: unknown): string {
    if (typeof input === 'object' && input !== null) {
      const entries = Object.entries(input as Record<string, unknown>);
      if (entries.length === 0) return '{}';
      const [key, val] = entries[0];
      const valStr = typeof val === 'string'
        ? (val.length > 60 ? val.slice(0, 60) + '\u2026' : val)
        : JSON.stringify(val)?.slice(0, 60) ?? '';
      const suffix = entries.length > 1 ? ` + ${entries.length - 1} more fields` : '';
      return `${key}: ${valStr}${suffix}`;
    }
    return String(input).slice(0, 80);
  }

  function formatInput(input: unknown): string {
    if (typeof input === 'string') return input;
    try {
      return JSON.stringify(input, null, 2);
    } catch {
      return String(input);
    }
  }

  function contentSummary(content: unknown[]): string {
    const first = content[0];
    if (typeof first === 'object' && first !== null && 'text' in (first as Record<string, unknown>)) {
      const text = (first as Record<string, unknown>).text as string;
      return text.length > 80 ? text.slice(0, 80) + '\u2026' : text;
    }
    return `${content.length} block${content.length !== 1 ? 's' : ''}`;
  }

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

  let detailsSummary = $derived((() => {
    if (call.input != null) return inputSummary(call.input);
    if (Array.isArray(call.content) && call.content.length > 0) return contentSummary(call.content);
    if (result?.content != null) {
      const text = formatContent(result.content);
      return text.length > 80 ? text.slice(0, 80) + '\u2026' : text;
    }
    return '';
  })());
</script>

<div class="tool-group" class:tool-group-completed={isCompleted} class:tool-group-error={isError} class:tool-group-running={isRunning}>
  <div class="tool-group-header">
    <span class="tool-group-icon">{'\u2699'}</span>
    <span class="tool-name">{title}</span>
    {#if displayStatus}
      <span class="tool-status" class:completed={isCompleted} class:failed={isError} class:running={isRunning}>{displayStatus}</span>
    {/if}
    {#if resultHint}
      <span class="tool-result-hint">{resultHint}</span>
    {/if}
  </div>
  {#if hasDetails}
    <details class="tool-group-details">
      <summary class="tool-group-summary">{detailsSummary}</summary>
      {#if call.input != null}
        <div class="tool-section">
          <div class="tool-section-label">Input</div>
          <pre class="tool-pre">{formatInput(call.input)}</pre>
        </div>
      {/if}
      {#if Array.isArray(call.content) && call.content.length > 0}
        <div class="tool-section">
          <div class="tool-section-label">Content</div>
          <pre class="tool-pre">{formatInput(call.content)}</pre>
        </div>
      {/if}
      {#if result?.content != null}
        <div class="tool-section">
          <div class="tool-section-label">{result.isError ? 'Error' : 'Result'}</div>
          <pre class="tool-pre" class:tool-pre-error={result.isError}>{formatContent(result.content)}</pre>
        </div>
      {/if}
    </details>
  {/if}
</div>

<style>
  .tool-group {
    padding: 0.5rem 0.75rem;
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.15);
    max-width: 90%;
  }

  .tool-group-completed {
    border-color: hsl(var(--status-success) / 0.3);
    background: hsl(var(--status-success) / 0.04);
  }

  .tool-group-error {
    border-color: hsl(var(--status-danger) / 0.3);
    background: hsl(var(--status-danger) / 0.04);
  }

  .tool-group-running {
    border-color: hsl(var(--status-working) / 0.3);
    background: hsl(var(--status-working) / 0.04);
  }

  .tool-group-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.8125rem;
  }

  .tool-group-icon {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .tool-name {
    font-weight: 500;
    color: hsl(var(--foreground));
  }

  .tool-status {
    font-size: 0.625rem;
    font-weight: 600;
    padding: 0.0625rem 0.375rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.03em;
    flex-shrink: 0;
  }

  .tool-status.completed {
    background: hsl(var(--status-success) / 0.15);
    color: hsl(var(--status-success));
  }

  .tool-status.failed {
    background: hsl(var(--status-danger) / 0.15);
    color: hsl(var(--status-danger));
  }

  .tool-status.running {
    background: hsl(var(--status-working) / 0.15);
    color: hsl(var(--status-working));
  }

  .tool-result-hint {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-group-details {
    margin-top: 0.375rem;
    font-size: 0.6875rem;
  }

  .tool-group-summary {
    cursor: pointer;
    color: hsl(var(--muted-foreground));
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    padding: 0.125rem 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tool-group-summary:hover {
    color: hsl(var(--foreground));
  }

  .tool-section {
    margin-top: 0.25rem;
  }

  .tool-section-label {
    font-size: 0.625rem;
    font-weight: 600;
    color: hsl(var(--muted-foreground));
    text-transform: uppercase;
    letter-spacing: 0.03em;
    margin-bottom: 0.125rem;
  }

  .tool-pre {
    padding: 0.375rem 0.5rem;
    border-radius: var(--radius-sm);
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

  .tool-pre-error {
    color: hsl(var(--status-danger));
  }
</style>
