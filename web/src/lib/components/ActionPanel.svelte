<script lang="ts">
  import DecisionQueue from './DecisionQueue.svelte';
  import { onMount } from 'svelte';

  interface Props {
    collapsed: boolean;
    onToggle: () => void;
    decisionCount: number;
    wide?: boolean;
  }

  let { collapsed, onToggle, decisionCount, wide = false }: Props = $props();

  const STORAGE_KEY = 'agentbeacon-action-panel-width';
  const DEFAULT_WIDTH = 420;
  const MIN_WIDTH = 280;
  const MAX_WIDTH = 700;
  const COLLAPSED_WIDTH = 40;

  let panelWidth = $state(DEFAULT_WIDTH);
  let isDragging = $state(false);
  let isMobile = $state(false);

  // Expose effective width as CSS custom property on :root so sibling panels
  // can reserve space without being in the same flex context.
  // Use a "committed" width that only updates when NOT dragging, so the
  // SplitPanel's percentage-based left panel doesn't jitter during resize.
  let committedWidth = $state(DEFAULT_WIDTH);
  $effect(() => {
    if (!isDragging) {
      committedWidth = isMobile ? 0 : (collapsed ? COLLAPSED_WIDTH : (wide ? 0 : panelWidth));
    }
  });
  $effect(() => {
    document.documentElement.style.setProperty('--action-panel-width', `${committedWidth}px`);
  });
  $effect(() => {
    const mql = window.matchMedia('(max-width: 768px)');
    isMobile = mql.matches;
    const handler = (e: MediaQueryListEvent) => { isMobile = e.matches; };
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  });

  function handleDragStart(e: MouseEvent) {
    if (collapsed) return;
    isDragging = true;
    e.preventDefault();
  }

  function handleMouseMove(e: MouseEvent) {
    if (!isDragging) return;
    const newWidth = window.innerWidth - e.clientX;
    panelWidth = Math.min(Math.max(newWidth, MIN_WIDTH), MAX_WIDTH);
  }

  function handleMouseUp() {
    if (isDragging) {
      isDragging = false;
      localStorage.setItem(STORAGE_KEY, panelWidth.toString());
    }
  }

  onMount(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const w = parseInt(saved, 10);
      if (!isNaN(w) && w >= MIN_WIDTH && w <= MAX_WIDTH) {
        panelWidth = w;
        committedWidth = collapsed ? COLLAPSED_WIDTH : (wide ? 0 : w);
      }
    }
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      document.documentElement.style.removeProperty('--action-panel-width');
    };
  });
</script>

<aside
  class="action-panel"
  class:collapsed
  class:wide
  class:mobile={isMobile}
  class:dragging={isDragging}
  aria-label="Decisions panel"
  style={!collapsed && !wide ? `width: ${panelWidth}px` : ''}
>
  {#if !collapsed && !wide}
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div
      class="resize-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize decisions panel"
      onmousedown={handleDragStart}
    ></div>
  {/if}

  {#if collapsed}
    <button class="collapsed-strip" onclick={onToggle} aria-label="Expand decisions panel">
      <div class="collapsed-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
          <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
        </svg>
        {#if decisionCount > 0}
          <span class="collapsed-badge">{decisionCount > 9 ? '9+' : decisionCount}</span>
        {/if}
      </div>
      <svg class="expand-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 18 9 12 15 6" />
      </svg>
    </button>
  {:else}
    <div class="panel-header">
      <span class="panel-title">DECISIONS ({decisionCount})</span>
      <button class="collapse-btn" onclick={onToggle} aria-label="Collapse decisions panel">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </button>
    </div>
    <div class="panel-body scroll-thin">
      <DecisionQueue />
    </div>
  {/if}
</aside>

{#if isMobile && !collapsed}
  <button class="mobile-backdrop" onclick={onToggle} aria-label="Close decisions panel"></button>
{/if}

{#if isDragging}
  <div class="drag-overlay"></div>
{/if}

<style>
  .action-panel {
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    width: 420px;
    display: flex;
    flex-direction: column;
    border-left: 1px solid hsl(var(--border));
    background: hsl(var(--background));
    overflow: visible;
    z-index: 5;
  }

  .action-panel:not(.dragging):not(.collapsed) {
    transition: width 0.15s ease;
  }

  .action-panel.collapsed {
    width: 40px;
  }

  .resize-handle {
    position: absolute;
    left: -10px;
    top: 0;
    bottom: 0;
    width: 20px;
    cursor: col-resize;
    z-index: 10;
    user-select: none;
    background: transparent;
  }

  .resize-handle::after {
    content: '';
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 3px;
    height: 100%;
    border-radius: 1.5px;
    background: hsl(var(--primary));
    opacity: 0;
    transition: opacity 0.15s ease;
  }

  .resize-handle:hover::after {
    opacity: 1;
  }

  .dragging .resize-handle::after {
    opacity: 0.6;
  }

  .drag-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999;
    cursor: col-resize;
  }

  .collapsed-strip {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    padding: 0.5rem 0;
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: color 0.15s, background 0.15s;
  }

  .collapsed-strip:hover {
    background: hsl(var(--muted) / 0.3);
    color: hsl(var(--foreground));
  }

  .collapsed-icon {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .collapsed-icon svg {
    width: 18px;
    height: 18px;
  }

  .collapsed-badge {
    position: absolute;
    top: -6px;
    right: -8px;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 8px;
    background: hsl(var(--status-danger));
    color: hsl(0 0% 100%);
    font-size: 0.625rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    line-height: 1;
  }

  .expand-chevron {
    width: 14px;
    height: 14px;
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid hsl(var(--border));
    flex-shrink: 0;
  }

  .panel-title {
    font-size: 0.6875rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: hsl(var(--muted-foreground));
  }

  .collapse-btn {
    width: 24px;
    height: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
    border: none;
    background: transparent;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    transition: color 0.15s, background 0.15s;
  }

  .collapse-btn:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .collapse-btn svg {
    width: 14px;
    height: 14px;
  }

  .panel-body {
    flex: 1;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .action-panel.wide {
    position: static;
    flex: 1 1 0;
    width: auto;
    min-width: 0;
    transition: none;
  }

  .mobile-backdrop {
    position: fixed;
    inset: 0;
    z-index: 49;
    background: rgba(0, 0, 0, 0.4);
    border: none;
    cursor: default;
  }

  @media (max-width: 768px) {
    .action-panel {
      position: fixed;
      z-index: 50;
      transition: transform 0.25s ease;
    }

    .action-panel.collapsed {
      display: none;
    }

    .action-panel:not(.collapsed) {
      top: 0;
      bottom: 0;
      right: 0;
      left: 0;
      width: 100% !important;
      border-left: none;
    }

  }
</style>
