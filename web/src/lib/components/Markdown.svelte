<script lang="ts">
  import { renderMarkdown } from '../markdown';

  interface Props { text: string; }
  let { text }: Props = $props();

  let html: string | null = $state(null);

  $effect(() => {
    const currentText = text;
    html = null;
    renderMarkdown(currentText).then(result => {
      if (text === currentText) html = result;
    }).catch(() => {
      // Fallback plain text is already displayed
    });
  });
</script>

{#if html}
  <div class="markdown-body">{@html html}</div>
{:else}
  <div class="markdown-body markdown-plain">{text}</div>
{/if}
