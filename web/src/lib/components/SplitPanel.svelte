<script lang="ts">
  import { onMount } from 'svelte';
  import type { Snippet } from 'svelte';

  interface Props {
    storageKey: string;
    initialLeftWidth?: number;
    minWidth?: number;
    maxWidth?: number;
    collapsed?: boolean;
    onresize?: (data: { leftWidth: number }) => void;
    left?: Snippet;
    right?: Snippet;
  }

  let { storageKey, initialLeftWidth = 50, minWidth = 20, maxWidth = 80, collapsed = false, onresize, left, right }: Props = $props();

  let leftPanelWidth = $state(initialLeftWidth);
  let isDragging = $state(false);
  let containerElement: HTMLDivElement;

  function handleDividerMouseDown(e: MouseEvent) {
    if (collapsed) return;
    isDragging = true;
    e.preventDefault();
  }

  function handleMouseMove(e: MouseEvent) {
    if (!isDragging || !containerElement) return;

    const containerRect = containerElement.getBoundingClientRect();
    const newLeftWidth = ((e.clientX - containerRect.left) / containerRect.width) * 100;

    // Constrain between minWidth and maxWidth
    leftPanelWidth = Math.min(Math.max(newLeftWidth, minWidth), maxWidth);

    // Emit resize event
    onresize?.({ leftWidth: leftPanelWidth });
  }

  function handleMouseUp() {
    if (isDragging) {
      isDragging = false;
      localStorage.setItem(storageKey, leftPanelWidth.toString());
    }
  }

  function handleDividerKeydown(e: KeyboardEvent) {
    if (collapsed) return;
    const step = 2;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
      e.preventDefault();
      leftPanelWidth = Math.max(leftPanelWidth - step, minWidth);
      localStorage.setItem(storageKey, leftPanelWidth.toString());
      onresize?.({ leftWidth: leftPanelWidth });
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
      e.preventDefault();
      leftPanelWidth = Math.min(leftPanelWidth + step, maxWidth);
      localStorage.setItem(storageKey, leftPanelWidth.toString());
      onresize?.({ leftWidth: leftPanelWidth });
    }
  }

  onMount(() => {
    // Load saved divider position from localStorage
    const savedPosition = localStorage.getItem(storageKey);
    if (savedPosition) {
      const position = parseFloat(savedPosition);
      if (!isNaN(position) && position >= minWidth && position <= maxWidth) {
        leftPanelWidth = position;
      }
    }

    // Add global mouse event listeners for resizing
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  });
</script>

<div bind:this={containerElement} class="split-panel-container" class:dragging={isDragging}>
  <div
    class="left-panel"
    class:collapsed
    inert={collapsed || undefined}
    style="width: {collapsed ? 0 : leftPanelWidth}%; min-width: {collapsed ? 0 : minWidth}%; max-width: {collapsed ? 0 : maxWidth}%;"
  >
    {#if left}{@render left()}{/if}
  </div>

  <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
  <div
    class="divider"
    class:hidden-divider={collapsed}
    role="separator"
    aria-orientation="vertical"
    aria-valuenow={Math.round(leftPanelWidth)}
    aria-valuemin={minWidth}
    aria-valuemax={maxWidth}
    aria-label="Resize panels"
    tabindex="0"
    onmousedown={handleDividerMouseDown}
    onkeydown={handleDividerKeydown}
  ></div>

  <div style="width: {collapsed ? 100 : 100 - leftPanelWidth}%; min-width: {collapsed ? 0 : minWidth}%;" class="right-panel">
    {#if right}{@render right()}{/if}
  </div>
</div>

<style>
  .split-panel-container {
    display: flex;
    gap: 0;
    min-height: 0;
    min-width: 0;
    flex: 1;
  }

  .left-panel,
  .right-panel {
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
    overflow: hidden;
  }

  .split-panel-container:not(.dragging) .left-panel {
    transition: width 0.2s ease, min-width 0.2s ease, max-width 0.2s ease, opacity 0.15s ease;
  }

  .left-panel.collapsed {
    overflow: hidden;
    opacity: 0;
    pointer-events: none;
  }

  .divider {
    width: 8px;
    cursor: col-resize;
    background: transparent;
    position: relative;
    user-select: none;
    margin: 0 4px;
    border: none;
    padding: 0;
    outline: none;
  }

  .hidden-divider {
    display: none;
  }

  .divider:focus-visible::after {
    content: '';
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 2px;
    height: 100%;
    background: hsl(var(--primary));
    opacity: 0.8;
  }

  .divider:hover::after,
  .dragging .divider::after {
    content: '';
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 2px;
    height: 100%;
    background: hsl(var(--primary));
    opacity: 0.5;
  }

  .divider:hover::after {
    opacity: 0.7;
  }

  .dragging {
    cursor: col-resize;
    user-select: none;
  }

  /* Responsive: Stack vertically on mobile */
  @media (max-width: 768px) {
    .split-panel-container {
      flex-direction: column;
    }

    .left-panel,
    .right-panel {
      width: 100% !important;
      min-width: 100% !important;
      max-width: 100% !important;
    }

    .divider {
      display: none;
    }
  }
</style>
