<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Theme } from '../types';
  import ThemeToggle from './ThemeToggle.svelte';

  export let theme: Theme;

  const dispatch = createEventDispatcher<{
    themeChange: { theme: Theme };
  }>();
</script>

<header class="app-header" class:dark={theme === 'dark'}>
  <div class="app-header-content">
    <div class="app-title">
      <span class="app-icon">🎭</span>
      <span class="app-name">AgentMaestro</span>
    </div>
    <div class="app-header-actions">
      <ThemeToggle on:themeChange />
    </div>
  </div>
</header>

<style>
  .app-header {
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
    height: 3rem;
    flex-shrink: 0;
  }

  .app-header.dark {
    background: #1e293b;
    border-bottom-color: #334155;
  }

  .app-header-content {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 100%;
    width: 100%;
    padding: 0 1.5rem;
  }

  .app-title {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-weight: 600;
    font-size: 1.125rem;
    color: #1e293b;
  }

  .app-header.dark .app-title {
    color: #e2e8f0;
  }

  .app-icon {
    font-size: 1.5rem;
    line-height: 1;
  }

  .app-name {
    user-select: none;
  }

  .app-header-actions {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  @media (max-width: 640px) {
    .app-header-content {
      padding: 0 1rem;
    }

    .app-name {
      display: none;
    }
  }
</style>
