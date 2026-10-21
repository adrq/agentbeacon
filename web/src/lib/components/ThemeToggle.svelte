<!-- SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import Button from './ui/button.svelte';
  import { onMount } from 'svelte';
  import { theme, applyTheme, safeGetItem } from '../stores/appState';

  // The store is the source of truth so this and the nav rail toggle stay in sync.
  let mode = $derived($theme);

  function toggle() {
    applyTheme(mode === 'dark' ? 'light' : 'dark');
  }

  onMount(() => {
    const stored = safeGetItem('theme');
    if (stored === 'light' || stored === 'dark') applyTheme(stored);
    else applyTheme(window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
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
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round" width="14" height="14">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
    Dark
  {:else}
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" stroke-linejoin="round" width="14" height="14">
      <circle cx="12" cy="12" r="5" />
      <line x1="12" y1="1" x2="12" y2="3" />
      <line x1="12" y1="21" x2="12" y2="23" />
      <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
      <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
      <line x1="1" y1="12" x2="3" y2="12" />
      <line x1="21" y1="12" x2="23" y2="12" />
      <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
      <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
    </svg>
    Light
  {/if}
</Button>
