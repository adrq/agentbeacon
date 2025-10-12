<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import Breadcrumb from './Breadcrumb.svelte';
  import type { BreadcrumbSegment, Theme } from '../types';

  export let breadcrumbSegments: BreadcrumbSegment[];
  export let theme: Theme;

  const dispatch = createEventDispatcher<{ navigate: { path: string } }>();

  function handleNavigate(event: CustomEvent<{ path: string }>) {
    dispatch('navigate', event.detail);
  }
</script>

<header class="screen-header" class:dark={theme === 'dark'}>
  <div class="header-content">
    {#if breadcrumbSegments.length > 0}
      <Breadcrumb segments={breadcrumbSegments} {theme} on:navigate={handleNavigate} />
    {/if}
  </div>
  <div class="header-actions">
    <slot name="actions" />
  </div>
</header>

<style>
  .screen-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1rem 1.5rem;
    background: #ffffff;
    border-bottom: 1px solid #e2e8f0;
    min-height: 3.5rem;
  }

  .screen-header.dark {
    background: #1e293b;
    border-bottom-color: #334155;
  }

  .header-content {
    flex: 1;
    min-width: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-shrink: 0;
    margin-left: 1rem;
  }

  /* Mobile responsive */
  @media (max-width: 768px) {
    .screen-header {
      padding: 0.75rem 1rem;
      flex-wrap: wrap;
    }

    .header-actions {
      margin-left: 0;
      margin-top: 0.5rem;
      width: 100%;
    }
  }
</style>
