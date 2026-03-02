<script lang="ts">
  import { untrack } from 'svelte';
  import { renderMarkdown } from '../markdown';

  interface Props { text: string; streaming?: boolean; }
  let { text, streaming = false }: Props = $props();

  let html: string | null = $state(null);
  let latestText = $state(text);
  // Monotonic counter to discard stale async renders. Incremented before each
  // renderMarkdown call; the resolving promise only writes html when its
  // captured gen still matches, preventing out-of-order overwrites.
  let renderGen = 0;

  // Always track the latest text for the interval callback to read
  $effect(() => { latestText = text; });

  // Streaming mode: interval-based throttled rendering.
  // The initial render and interval callback read latestText inside untrack/callback
  // so this effect only re-runs when `streaming` changes (not on every text chunk).
  $effect(() => {
    if (streaming) {
      untrack(() => {
        const gen = ++renderGen;
        renderMarkdown(latestText, true).then(r => { if (gen === renderGen) html = r; }).catch(() => {});
      });
      const interval = setInterval(() => {
        const gen = ++renderGen;
        renderMarkdown(latestText, true).then(r => { if (gen === renderGen) html = r; }).catch(() => {});
      }, 300);
      return () => clearInterval(interval);
    }
  });

  // Non-streaming mode: immediate render (existing behavior)
  $effect(() => {
    if (!streaming) {
      const gen = ++renderGen;
      renderMarkdown(text).then(result => {
        if (gen === renderGen) html = result;
      }).catch(() => {});
    }
  });
</script>

{#if html}
  <div class="markdown-body">{@html html}</div>
{:else}
  <div class="markdown-body markdown-plain">{text}</div>
{/if}
