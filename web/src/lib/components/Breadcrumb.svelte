<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import type { BreadcrumbSegment, Theme } from '../types';

  export let segments: BreadcrumbSegment[];
  export let theme: Theme;

  const dispatch = createEventDispatcher<{ navigate: { path: string } }>();

  function handleClick(path: string) {
    dispatch('navigate', { path });
  }
</script>

<nav class="breadcrumb" class:dark={theme === 'dark'} aria-label="Breadcrumb">
  {#each segments as segment, index}
    {#if index > 0}
      <span class="separator" aria-hidden="true"> > </span>
    {/if}
    <button
      class="breadcrumb-link"
      on:click={() => handleClick(segment.path)}
      type="button"
    >
      {segment.label}
    </button>
  {/each}
</nav>

<style>
  .breadcrumb {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.875rem;
    color: #64748b;
    flex-wrap: wrap;
  }

  .breadcrumb.dark {
    color: #94a3b8;
  }

  .breadcrumb-link {
    background: none;
    border: none;
    padding: 0;
    color: inherit;
    cursor: pointer;
    text-decoration: none;
    transition: color 0.2s ease;
    font-size: inherit;
    font-weight: 500;
  }

  .breadcrumb-link:hover {
    color: #3b82f6;
    text-decoration: underline;
  }

  .breadcrumb.dark .breadcrumb-link:hover {
    color: #60a5fa;
  }

  .separator {
    color: #cbd5e1;
    user-select: none;
  }

  .breadcrumb.dark .separator {
    color: #475569;
  }

  /* Mobile responsive: truncate long breadcrumbs */
  @media (max-width: 480px) {
    .breadcrumb {
      font-size: 0.8125rem;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex-wrap: nowrap;
    }
  }
</style>
