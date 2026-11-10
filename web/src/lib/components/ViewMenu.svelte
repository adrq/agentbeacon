<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import Button from './ui/button.svelte';
  import { EVENT_FILTER_PILLS, type EventFilter } from '../eventFilterGroups';
  import { VIEW_OPTIONS, type ViewMode } from '../viewMode';
  import { menuNav } from '../menuNav';

  interface Props {
    viewMode: ViewMode;
    eventFilter: EventFilter;
    onview: (mode: ViewMode) => void;
    onfilter: (filter: EventFilter) => void;
  }

  let { viewMode, eventFilter, onview, onfilter }: Props = $props();

  let open = $state(false);
  let wrapper: HTMLDivElement | undefined = $state();
  let trigger = $derived(wrapper?.querySelector<HTMLElement>('button[aria-haspopup]') ?? null);
</script>

<div class="view-menu" bind:this={wrapper}>
  <Button variant="ghost" size="sm" aria-haspopup="menu" aria-expanded={open} onclick={() => { open = !open; }}>
    View
    <svg class="view-menu-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9" /></svg>
  </Button>
  {#if open}
    <div class="view-menu-popover" role="menu" aria-label="View" tabindex="-1" use:menuNav={{ trigger, initialFocus: 'checked', onclose: () => { open = false; } }}>
      <div class="view-menu-section" role="group" aria-label="View mode">
        {#each VIEW_OPTIONS as option}
          <button
            class="view-menu-item"
            role="menuitemradio"
            aria-checked={viewMode === option.value}
            tabindex="-1"
            onclick={() => onview(option.value)}
          >{option.label}</button>
        {/each}
      </div>
      {#if viewMode !== 'diff'}
        <div class="view-menu-sep" role="separator"></div>
        <div class="view-menu-section" role="group" aria-label="Filter">
          {#each EVENT_FILTER_PILLS as pill}
            <button
              class="view-menu-item"
              role="menuitemradio"
              aria-checked={eventFilter === pill.value}
              tabindex="-1"
              onclick={() => onfilter(pill.value)}
            >{pill.label}</button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .view-menu {
    position: relative;
    display: inline-flex;
  }

  .view-menu-caret {
    width: 12px;
    height: 12px;
  }

  .view-menu-popover {
    position: absolute;
    top: 100%;
    right: 0;
    margin-top: 0.25rem;
    min-width: 9rem;
    padding: 0.25rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
    box-shadow: 0 4px 12px hsl(var(--shadow-hsl) / 0.2);
    z-index: 60;
    display: flex;
    flex-direction: column;
    outline: none;
  }

  .view-menu-section {
    display: flex;
    flex-direction: column;
    gap: 0.0625rem;
  }

  .view-menu-sep {
    height: 1px;
    margin: 0.25rem 0;
    background: hsl(var(--border));
  }

  .view-menu-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.625rem;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--foreground));
    font-size: 0.6875rem;
    font-weight: 500;
    text-align: left;
    cursor: pointer;
    outline: none;
  }

  .view-menu-item::before {
    content: '';
    width: 0.375rem;
    height: 0.375rem;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .view-menu-item[aria-checked='true']::before {
    background: hsl(var(--foreground));
  }

  .view-menu-item:hover,
  .view-menu-item:focus-visible {
    background: hsl(var(--muted) / 0.5);
  }
</style>
