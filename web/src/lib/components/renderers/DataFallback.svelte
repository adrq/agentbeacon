<script lang="ts">
  interface Props {
    data: Record<string, unknown>;
  }

  let { data }: Props = $props();

  let dataType = $derived((data.type as string) ?? 'data');
  let jsonText = $derived.by(() => {
    try {
      return JSON.stringify(data, null, 2);
    } catch {
      return String(data);
    }
  });
</script>

<div class="fallback-card">
  <details class="fallback-details">
    <summary class="fallback-summary">
      <span class="fallback-icon">{'\u25A1'}</span>
      <span class="fallback-type">[{dataType}]</span>
    </summary>
    <pre class="fallback-json">{jsonText}</pre>
  </details>
</div>

<style>
  .fallback-card {
    max-width: 85%;
    font-size: 0.6875rem;
  }

  .fallback-details {
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.1);
  }

  .fallback-summary {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.25rem 0.625rem;
    cursor: pointer;
    color: hsl(var(--muted-foreground));
    font-size: 0.6875rem;
  }

  .fallback-summary:hover {
    background: hsl(var(--muted) / 0.2);
  }

  .fallback-icon {
    font-size: 0.6875rem;
    flex-shrink: 0;
  }

  .fallback-type {
    font-family: var(--font-mono);
    font-size: 0.6875rem;
  }

  .fallback-json {
    padding: 0.375rem 0.625rem;
    font-family: var(--font-mono);
    font-size: 0.6875rem;
    line-height: 1.4;
    color: hsl(var(--muted-foreground));
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 20rem;
    overflow-y: auto;
    border-top: 1px solid hsl(var(--border));
  }
</style>
