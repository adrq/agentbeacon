<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { Theme } from '../types';

  export let title: string;
  export let expanded: boolean = false;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{ toggle: { expanded: boolean } }>();

  function handleToggle() {
    expanded = !expanded;
    dispatch('toggle', { expanded });
  }
</script>

<div class="collapsible-section" class:dark={theme === 'dark'}>
  <button class="section-header" on:click={handleToggle} aria-expanded={expanded}>
    <span class="expand-icon" aria-hidden="true">{expanded ? '▼' : '▶'}</span>
    <h3 class="section-title">{title}</h3>
  </button>

  <div class="section-content" class:expanded>
    {#if expanded}
      <div class="content-inner">
        <slot />
      </div>
    {/if}
  </div>
</div>

<style>
  .collapsible-section {
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
    overflow: hidden;
    background: #ffffff;
  }

  .collapsible-section.dark {
    border-color: #334155;
    background: #1e293b;
  }

  .section-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    width: 100%;
    padding: 0.875rem 1rem;
    background: #f8fafc;
    border: none;
    cursor: pointer;
    transition: background-color 0.2s ease;
  }

  .section-header:hover {
    background: #f1f5f9;
  }

  .collapsible-section.dark .section-header {
    background: #0f172a;
  }

  .collapsible-section.dark .section-header:hover {
    background: #1e293b;
  }

  .expand-icon {
    font-size: 0.75rem;
    color: #64748b;
    transition: transform 0.2s ease;
  }

  .collapsible-section.dark .expand-icon {
    color: #94a3b8;
  }

  .section-title {
    margin: 0;
    font-size: 0.9375rem;
    font-weight: 600;
    color: #0f172a;
    flex: 1;
    text-align: left;
  }

  .collapsible-section.dark .section-title {
    color: #e2e8f0;
  }

  .section-content {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease-out;
  }

  .section-content.expanded {
    max-height: 1000px;
  }

  .content-inner {
    padding: 1rem;
    border-top: 1px solid #e2e8f0;
  }

  .collapsible-section.dark .content-inner {
    border-top-color: #334155;
  }
</style>
