<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Theme } from '../types';

  export let tabs: string[];
  export let activeTabIndex: number;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{ tabChange: { index: number; label: string } }>();

  function handleTabClick(index: number, label: string) {
    dispatch('tabChange', { index, label });
  }
</script>

<div class="tab-navigation" class:dark={theme === 'dark'}>
  <div class="tab-bar">
    {#each tabs as tab, index}
      <button
        class="tab-button"
        class:active={index === activeTabIndex}
        on:click={() => handleTabClick(index, tab)}
        aria-selected={index === activeTabIndex}
        role="tab"
      >
        {tab}
      </button>
    {/each}
  </div>
</div>

<style>
  .tab-navigation {
    --tab-bg: #ffffff;
    --tab-border: #e2e8f0;
    --tab-text: #64748b;
    --tab-active-bg: #f8fafc;
    --tab-active-text: #0f172a;
    --tab-active-border: #3b82f6;
    --tab-hover-bg: #f1f5f9;
  }

  .tab-navigation.dark {
    --tab-bg: #1e293b;
    --tab-border: #334155;
    --tab-text: #94a3b8;
    --tab-active-bg: #0f172a;
    --tab-active-text: #e2e8f0;
    --tab-active-border: #3b82f6;
    --tab-hover-bg: #334155;
  }

  .tab-bar {
    display: flex;
    gap: 0;
    border-bottom: 2px solid var(--tab-border);
    background: var(--tab-bg);
    overflow-x: auto;
    overflow-y: hidden;
    -webkit-overflow-scrolling: touch;
  }

  /* Hide scrollbar but keep functionality */
  .tab-bar::-webkit-scrollbar {
    height: 0;
  }

  .tab-button {
    padding: 0.75rem 1.5rem;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--tab-text);
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
    white-space: nowrap;
    flex-shrink: 0;
    position: relative;
    margin-bottom: -2px;
  }

  .tab-button:hover {
    background: var(--tab-hover-bg);
    color: var(--tab-active-text);
  }

  .tab-button.active {
    background: var(--tab-active-bg);
    color: var(--tab-active-text);
    border-bottom-color: var(--tab-active-border);
    font-weight: 600;
  }

  .tab-button:focus-visible {
    outline: 2px solid var(--tab-active-border);
    outline-offset: -2px;
  }

  /* Mobile responsive */
  @media (max-width: 480px) {
    .tab-button {
      padding: 0.625rem 1rem;
      font-size: 0.8125rem;
    }
  }
</style>
