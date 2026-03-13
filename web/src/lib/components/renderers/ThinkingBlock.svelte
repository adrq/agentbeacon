<script lang="ts">
  import type { NormalizedThinking } from '../../normalize';

  interface Props {
    data: NormalizedThinking;
    compact?: boolean;
    isStreaming?: boolean;
    durationMs?: number;
  }

  let { data, compact = false, isStreaming = false, durationMs = 0 }: Props = $props();

  let text = $derived(data.text ?? '');

  let expanded = $state(false);
  $effect.pre(() => {
    if (isStreaming) expanded = true;
  });

  let durationText = $derived.by(() => {
    if (isStreaming || durationMs < 1000) return '';
    const secs = Math.round(durationMs / 1000);
    return `Thought for ${secs}s`;
  });
</script>

{#if compact}
  <span class="compact-thinking">
    <span class="think-icon">{'\u22EF'}</span>
    <span class="think-label">{text.length > 200 ? text.slice(0, 200) + '\u2026' : text}</span>
  </span>
{:else}
  <div class="thinking-block" class:expanded class:streaming={isStreaming}>
    <button class="thinking-header" onclick={() => expanded = !expanded}>
      <span class="think-icon">{'\u22EF'}</span>
      <span class="think-title">{isStreaming ? 'Thinking...' : (durationText || 'Thinking...')}</span>
      <span class="think-toggle">{expanded ? '\u25B2' : '\u25BC'}</span>
    </button>
    <div class="thinking-body">
      <div class="thinking-text">{text}</div>
    </div>
  </div>
{/if}

<style>
  .compact-thinking {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    color: hsl(var(--muted-foreground));
    font-style: italic;
  }

  .think-icon {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .think-label {
    color: hsl(var(--muted-foreground));
  }

  .thinking-block {
    border-radius: var(--radius);
    border: 1px solid hsl(var(--border));
    background: hsl(var(--muted) / 0.1);
    max-width: 85%;
    overflow: hidden;
  }

  @keyframes thinking-shimmer {
    0% { border-color: hsl(var(--border)); }
    50% { border-color: hsl(var(--muted-foreground) / 0.4); }
    100% { border-color: hsl(var(--border)); }
  }

  .thinking-block.streaming {
    border-width: 2px;
    animation: thinking-shimmer 2s ease-in-out infinite;
  }

  .thinking-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    width: 100%;
    padding: 0.375rem 0.75rem;
    border: none;
    background: transparent;
    cursor: pointer;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    font-style: italic;
    text-align: left;
  }

  .thinking-header:hover {
    background: hsl(var(--muted) / 0.2);
  }

  .think-title {
    flex: 1;
  }

  .think-toggle {
    font-size: 0.625rem;
    opacity: 0.6;
    flex-shrink: 0;
  }

  .thinking-body {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.25s ease;
  }

  .expanded .thinking-body {
    max-height: 200rem;
  }

  .thinking-text {
    padding: 0 0.75rem 0.5rem;
    font-size: 0.6875rem;
    line-height: 1.5;
    color: hsl(var(--muted-foreground));
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
