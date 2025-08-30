<script lang="ts">
  import Button from './ui/button.svelte';
  import { onMount } from 'svelte';

  // Single source of truth for storage key
  const STORAGE_KEY = 'agentmaestro-theme';
  let mode: 'dark' | 'light' = 'dark';

  function applyMode() {
    const root = document.documentElement.classList;
    if (mode === 'light') root.add('light'); else root.remove('light');
    try { localStorage.setItem(STORAGE_KEY, mode); } catch {}
  }

  function toggle() {
  mode = mode === 'dark' ? 'light' : 'dark';
  console.debug('[ThemeToggle] toggled ->', mode);
  applyMode();
  }

  onMount(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved === 'light' || saved === 'dark') mode = saved as any;
      else if (window.matchMedia('(prefers-color-scheme: light)').matches) mode = 'light';
    } catch {}
    applyMode();
    // Fallback listener in case Svelte binding fails (observed no state change on click)
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        toggle();
      });
    }
  });
</script>

<Button
  variant="secondary"
  size="sm"
  id="theme-toggle"
  aria-label={mode === 'dark' ? 'Activate light theme' : 'Activate dark theme'}
  aria-pressed={mode === 'light'}
  title={mode === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
>
  {#if mode === 'dark'}
    🌞 Light
  {:else}
    🌙 Dark
  {/if}
</Button>
