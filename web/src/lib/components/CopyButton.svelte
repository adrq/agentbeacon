<script lang="ts">
  interface Props {
    text: string;
    label?: string;
  }

  let { text, label = 'Copy' }: Props = $props();
  let copied = $state(false);

  async function handleClick(e: Event) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      return;
    }
    copied = true;
    setTimeout(() => { copied = false; }, 1500);
  }
</script>

<button
  class="copy-btn"
  class:copied
  aria-label={copied ? 'Copied' : label}
  onclick={handleClick}
>
  {#if copied}
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  {:else}
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <rect x="4" y="1" width="7" height="8" rx="1" stroke="currentColor" stroke-width="1.2"/>
      <path d="M1 4v7a1 1 0 001 1h6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
    </svg>
  {/if}
</button>

<style>
  .copy-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1rem;
    height: 1rem;
    padding: 0;
    border: none;
    border-radius: 2px;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    flex-shrink: 0;
    transition: color 0.15s;
  }

  .copy-btn:hover {
    color: hsl(var(--foreground));
  }

  .copy-btn.copied {
    color: hsl(var(--status-success));
  }
</style>
