<script lang="ts">
  import { onMount, createEventDispatcher } from 'svelte';

  // Props
  export let storageKey: string;
  export let initialLeftWidth = 50;
  export let minWidth = 20;
  export let maxWidth = 80;

  const dispatch = createEventDispatcher<{ resize: { leftWidth: number } }>();

  // State
  let leftPanelWidth = initialLeftWidth;
  let isDragging = false;
  let containerElement: HTMLDivElement;

  function handleDividerMouseDown(e: MouseEvent) {
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
    dispatch('resize', { leftWidth: leftPanelWidth });
  }

  function handleMouseUp() {
    if (isDragging) {
      isDragging = false;
      // Save to localStorage when dragging stops
      localStorage.setItem(storageKey, leftPanelWidth.toString());
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
  <div style="width: {leftPanelWidth}%; min-width: {minWidth}%; max-width: {maxWidth}%;" class="left-panel">
    <slot name="left" />
  </div>

  <!-- Draggable divider -->
  <button
    class="divider"
    on:mousedown={handleDividerMouseDown}
    aria-label="Resize panels"
  ></button>

  <div style="width: {100 - leftPanelWidth}%; min-width: {minWidth}%;" class="right-panel">
    <slot name="right" />
  </div>
</div>

<style>
  .split-panel-container {
    display: flex;
    gap: 0;
    min-height: 0;
    flex: 1;
  }

  .left-panel,
  .right-panel {
    display: flex;
    flex-direction: column;
    min-height: 0;
  }

  .divider {
    width: 8px;
    cursor: col-resize;
    background: transparent;
    position: relative;
    user-select: none;
    margin: 0 4px;
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
