<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import type { Snippet } from 'svelte';
  import { sidebarOpen } from '../stores/appState';

  interface Props {
    title: string;
    titleClass?: string;
    // For sections without a sidebar, where the brand lockup always overlays this row.
    noSidebar?: boolean;
    badges?: Snippet;
    actions?: Snippet;
  }

  let { title, titleClass = '', noSidebar = false, badges, actions }: Props = $props();

  let reserved = $derived(noSidebar || !$sidebarOpen);
</script>

<div class="page-header" class:brand-reserved={reserved}>
  {#if reserved}
    <span class="crumb-sep" aria-hidden="true">/</span>
  {/if}
  <h2 class="page-title {titleClass}">{title}</h2>
  {#if badges}
    <div class="page-badges">{@render badges()}</div>
  {/if}
  {#if actions}
    <div class="page-actions">{@render actions()}</div>
  {/if}
</div>

<style>
  /* Matches the execution header: one row beside the brand lockup. */
  .page-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 1rem;
    flex-shrink: 0;
    box-sizing: border-box;
    min-height: var(--top-row-height);
    border-bottom: 1px solid hsl(var(--border));
    transition: padding-left 0.2s ease;
  }

  /* With the sidebar collapsed, the brand lockup in AppShell overlays the start of this row. */
  .page-header.brand-reserved {
    padding-left: calc(var(--brand-lockup-width) - var(--rail-width) + 0.5rem);
  }

  .crumb-sep {
    font-size: 0.875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .page-title {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin: 0;
  }

  .page-badges {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    flex-shrink: 0;
  }

  .page-actions {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    margin-left: auto;
    flex-shrink: 0;
  }

  @media (max-width: 768px) {
    .crumb-sep {
      display: none;
    }
    .page-header.brand-reserved {
      padding-left: 1rem;
    }
  }
</style>
