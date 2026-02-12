<script lang="ts">
  import Button from './ui/button.svelte';
  import { onMount } from 'svelte';
  import { theme } from '../stores/appState';

  let mode: 'dark' | 'light' = $state('dark');

  function applyMode() {
    const root = document.documentElement.classList;
    if (mode === 'light') root.add('light'); else root.remove('light');
    theme.set(mode);
  }

  function toggle() {
    mode = mode === 'dark' ? 'light' : 'dark';
    applyMode();
  }

  onMount(() => {
    try {
      const stored = localStorage.getItem('theme');
      if (stored === 'light' || stored === 'dark') mode = stored;
      else if (window.matchMedia('(prefers-color-scheme: light)').matches) mode = 'light';
    } catch {}
    applyMode();
  });
</script>

<Button
  variant="secondary"
  size="sm"
  onclick={toggle}
  aria-label={mode === 'dark' ? 'Activate light theme' : 'Activate dark theme'}
  aria-pressed={mode === 'light'}
  title={mode === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
>
  {#if mode === 'dark'}
    Light
  {:else}
    Dark
  {/if}
</Button>
