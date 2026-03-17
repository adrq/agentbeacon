<script lang="ts">
  import { untrack, tick } from 'svelte';
  import { get } from 'svelte/store';
  import { renderMarkdown } from '../markdown';
  import { theme } from '../stores/appState';

  interface Props { text: string; streaming?: boolean; }
  let { text, streaming = false }: Props = $props();

  let html: string | null = $state(null);
  let latestText = $state(text);
  let containerEl: HTMLDivElement | undefined = $state();
  let currentTheme = $state(get(theme));
  // Monotonic counters to discard stale async renders. Incremented before each
  // async call; the resolving promise only applies when its captured gen still
  // matches, preventing out-of-order overwrites.
  let renderGen = 0;
  let mermaidGen = 0;

  // Track theme changes
  $effect(() => {
    const unsub = theme.subscribe(t => { currentTheme = t; });
    return unsub;
  });

  // Always track the latest text for the interval callback to read
  $effect(() => { latestText = text; });

  // Streaming mode: interval-based throttled rendering.
  // The initial render and interval callback read latestText inside untrack/callback
  // so this effect only re-runs when `streaming` changes (not on every text chunk).
  $effect(() => {
    if (streaming) {
      untrack(() => {
        const gen = ++renderGen;
        renderMarkdown(latestText, true, true).then(r => { if (gen === renderGen) html = r; }).catch((err) => { console.warn('streaming render failed:', err); });
      });
      const interval = setInterval(() => {
        const gen = ++renderGen;
        renderMarkdown(latestText, true, true).then(r => { if (gen === renderGen) html = r; }).catch((err) => { console.warn('streaming render failed:', err); });
      }, 300);
      return () => clearInterval(interval);
    }
  });

  // Non-streaming mode: immediate render with retry and fallback
  $effect(() => {
    if (!streaming) {
      const gen = ++renderGen;
      let timeoutId: ReturnType<typeof setTimeout> | null = null;
      const attempt = (retries: number) => {
        renderMarkdown(text).then(result => {
          if (gen === renderGen) html = result;
        }).catch((err) => {
          console.warn('renderMarkdown failed, retries left:', retries, err);
          if (retries > 0 && gen === renderGen) {
            timeoutId = setTimeout(() => attempt(retries - 1), 500);
          } else if (gen === renderGen) {
            // Fallback: render without Shiki rather than showing raw text
            renderMarkdown(text, false, true).then(result => {
              if (gen === renderGen) html = result;
            }).catch((e) => { console.warn('renderMarkdown fallback also failed:', e); });
          }
        });
      };
      attempt(3);
      return () => {
        if (timeoutId !== null) clearTimeout(timeoutId);
      };
    }
  });

  // Fade in Shiki blocks after HTML is injected (runs when html changes and not streaming)
  $effect(() => {
    if (!streaming && html && containerEl) {
      tick().then(() => {
        if (containerEl) {
          containerEl.querySelectorAll<HTMLPreElement>('pre.shiki').forEach(el => {
            el.classList.add('shiki-visible');
          });
        }
      });
    }
  });

  // Render mermaid diagrams after HTML is injected into the DOM.
  // Lazy-imports mermaid only when a diagram is present to avoid loading
  // the ~800KB library when no diagrams exist.
  // Also re-runs when theme changes: restores original <pre> source, then re-renders.
  $effect(() => {
    const _theme = currentTheme; // reactive dependency on theme
    if (!html || !containerEl) return;
    if (streaming) return; // Skip mermaid rendering during streaming
    const gen = ++mermaidGen;

    tick().then(async () => {
      if (gen !== mermaidGen || !containerEl) return;

      // Restore any previously-rendered mermaid SVGs back to <pre> source
      containerEl.querySelectorAll<HTMLPreElement>('[data-mermaid-source]').forEach(el => {
        const pre = document.createElement('pre');
        pre.className = 'mermaid';
        pre.textContent = el.getAttribute('data-mermaid-source')!;
        el.replaceWith(pre);
      });

      const nodes = containerEl.querySelectorAll<HTMLPreElement>('pre.mermaid');
      if (nodes.length === 0) return;

      // Stash original source before mermaid replaces the elements
      nodes.forEach(n => n.setAttribute('data-mermaid-source', n.textContent ?? ''));

      try {
        const { default: mermaid } = await import('mermaid');
        if (gen !== mermaidGen) return;
        mermaid.initialize({ startOnLoad: false, suppressErrorRendering: true, theme: _theme === 'light' ? 'default' : 'dark' });
        await mermaid.run({ nodes, suppressErrors: true });
      } catch {
        // Malformed diagrams degrade to plain <pre> text (already in DOM)
      }
    });
  });
</script>

{#if html}
  <div class="markdown-body" bind:this={containerEl}>{@html html}</div>
{:else}
  <div class="markdown-body markdown-plain">{text}</div>
{/if}

<style>
  /* Fade-in transition for Shiki-highlighted code blocks when settling */
  :global(.markdown-body pre.shiki) {
    opacity: 0;
    transition: opacity 0.3s ease;
  }
  :global(.markdown-body pre.shiki.shiki-visible) {
    opacity: 1;
  }
</style>
