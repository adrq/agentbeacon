<script lang="ts">
  import { onDestroy, tick } from 'svelte';
  import type { SessionSummary, Agent, SessionIdentity } from '../types';
  import { buildTree, applyCompaction, computeLayout, edgePath } from '../utils/treeLayout';
  import OrgChartNode from './OrgChartNode.svelte';
  import { selectedSessionId } from '../stores/appState';

  interface Props {
    sessions: SessionSummary[];
    agents: Agent[];
    sessionIdentity?: Map<string, SessionIdentity>;
    onselectsession?: (sessionId: string) => void;
    onstatuschange?: () => void;
  }

  let { sessions, agents, sessionIdentity, onselectsession, onstatuschange }: Props = $props();

  // Ticking clock for elapsed time (updates every 15s)
  let now = $state(Date.now());
  const tickInterval = setInterval(() => { now = Date.now(); }, 15000);
  onDestroy(() => clearInterval(tickInterval));

  // Precomputed agent name lookup
  let agentNameById = $derived(new Map(agents.map(a => [a.id, a.name])));

  let collapsedNodes = $state<Set<string>>(new Set());

  function toggleCollapse(sessionId: string) {
    const next = new Set(collapsedNodes);
    if (next.has(sessionId)) next.delete(sessionId);
    else next.add(sessionId);
    collapsedNodes = next;
    // Re-fit after collapse/expand
    shouldRefit = true;
  }

  let tree = $derived(buildTree(sessions));
  let compacted = $derived(applyCompaction(tree, collapsedNodes));
  let layout = $derived(computeLayout(compacted));

  const PADDING = 40;
  let canvasWidth = $derived(layout.bounds.width + PADDING * 2);
  let canvasHeight = $derived(layout.bounds.height + PADDING * 2);

  // Pan/zoom state
  let scale = $state(1);
  let panX = $state(0);
  let panY = $state(0);
  let containerEl: HTMLDivElement | undefined = $state(undefined);
  let shouldRefit = $state(true);
  let isPanning = $state(false);
  let lastPointer = $state({ x: 0, y: 0 });

  const MIN_SCALE = 0.1;
  const MAX_SCALE = 1.5;
  const MIN_FIT_SCALE = 0.35;

  function computeFitScale(): { scale: number; panX: number; panY: number } {
    if (!containerEl) return { scale: 1, panX: 0, panY: 0 };
    const cw = containerEl.clientWidth;
    const ch = containerEl.clientHeight;
    if (cw === 0 || ch === 0) return { scale: 1, panX: 0, panY: 0 };

    const fitS = Math.max(MIN_FIT_SCALE, Math.min(1, cw / canvasWidth, ch / canvasHeight));
    const scaledW = canvasWidth * fitS;
    const scaledH = canvasHeight * fitS;
    // Center; if wider than container, center on the root (middle of canvas)
    const px = (cw - scaledW) / 2;
    const py = scaledH <= ch ? (ch - scaledH) / 2 : 0;
    return { scale: fitS, panX: px, panY: py };
  }

  function fitToView() {
    const fit = computeFitScale();
    scale = fit.scale;
    panX = fit.panX;
    panY = fit.panY;
  }

  // Refit when layout changes
  $effect(() => {
    // Access reactive deps
    canvasWidth;
    canvasHeight;
    if (shouldRefit) {
      tick().then(() => {
        fitToView();
        shouldRefit = false;
      });
    }
  });

  // Initial fit on mount
  $effect(() => {
    if (containerEl && shouldRefit) {
      fitToView();
      shouldRefit = false;
    }
  });

  function handleWheel(e: WheelEvent) {
    e.preventDefault();
    if (!containerEl) return;

    const rect = containerEl.getBoundingClientRect();
    const cursorX = e.clientX - rect.left;
    const cursorY = e.clientY - rect.top;

    // Point in canvas-space before zoom
    const canvasX = (cursorX - panX) / scale;
    const canvasY = (cursorY - panY) / scale;

    const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
    const newScale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale * zoomFactor));

    // Adjust pan so cursor stays over the same canvas point
    panX = cursorX - canvasX * newScale;
    panY = cursorY - canvasY * newScale;
    scale = newScale;
  }

  function handlePointerDown(e: PointerEvent) {
    // Left-click or middle-click to pan
    if (e.button !== 0 && e.button !== 1) return;
    // Don't pan if clicking on interactive elements inside nodes
    const target = e.target as HTMLElement;
    if (target.closest('button') || target.closest('a')) return;

    isPanning = true;
    lastPointer = { x: e.clientX, y: e.clientY };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }

  function handlePointerMove(e: PointerEvent) {
    if (!isPanning) return;
    const dx = e.clientX - lastPointer.x;
    const dy = e.clientY - lastPointer.y;
    panX += dx;
    panY += dy;
    lastPointer = { x: e.clientX, y: e.clientY };
  }

  function handlePointerUp(e: PointerEvent) {
    if (isPanning) {
      isPanning = false;
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    }
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
  class="org-chart-scroll"
  class:is-panning={isPanning}
  id="execution-overview"
  bind:this={containerEl}
  onwheel={handleWheel}
  onpointerdown={handlePointerDown}
  onpointermove={handlePointerMove}
  onpointerup={handlePointerUp}
  onpointercancel={handlePointerUp}
>
  <div class="org-chart-controls">
    <button class="zoom-btn" onclick={() => fitToView()} title="Fit to view">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <rect x="2" y="2" width="12" height="12" rx="1" stroke="currentColor" stroke-width="1.5" fill="none"/>
        <path d="M5 4L2 2M11 4L14 2M5 12L2 14M11 12L14 14" stroke="currentColor" stroke-width="1.2"/>
      </svg>
    </button>
    <button class="zoom-btn" onclick={() => { const cX = (containerEl?.clientWidth ?? 0) / 2; const cY = (containerEl?.clientHeight ?? 0) / 2; const canvasX = (cX - panX) / scale; const canvasY = (cY - panY) / scale; const ns = Math.min(MAX_SCALE, scale * 1.3); panX = cX - canvasX * ns; panY = cY - canvasY * ns; scale = ns; }} title="Zoom in">+</button>
    <button class="zoom-btn" onclick={() => { const cX = (containerEl?.clientWidth ?? 0) / 2; const cY = (containerEl?.clientHeight ?? 0) / 2; const canvasX = (cX - panX) / scale; const canvasY = (cY - panY) / scale; const ns = Math.max(MIN_SCALE, scale / 1.3); panX = cX - canvasX * ns; panY = cY - canvasY * ns; scale = ns; }} title="Zoom out">&minus;</button>
  </div>

  <div
    class="org-chart-canvas"
    style="width: {canvasWidth}px; height: {canvasHeight}px; transform: translate({panX}px, {panY}px) scale({scale}); transform-origin: 0 0;"
  >
    <svg
      width={canvasWidth}
      height={canvasHeight}
      style="pointer-events: none; position: absolute; top: 0; left: 0;"
    >
      {#each layout.edges as edge}
        <path
          d={edgePath(
            { ...edge.from, x: edge.from.x + PADDING, y: edge.from.y + PADDING },
            { ...edge.to, x: edge.to.x + PADDING, y: edge.to.y + PADDING }
          )}
          stroke="hsl(var(--muted-foreground) / 0.3)"
          stroke-width="1.5"
          fill="none"
        />
      {/each}
    </svg>

    {#each layout.nodes as node (node.session.id)}
      <div style="position: absolute; left: {node.x + PADDING}px; top: {node.y + PADDING}px;">
        <OrgChartNode
          session={node.session}
          {agentNameById}
          {sessionIdentity}
          {now}
          selected={$selectedSessionId === node.session.id}
          collapsed={collapsedNodes.has(node.session.id)}
          collapsedChildCount={node.collapsedChildCount ?? 0}
          hasChildren={node.children.length > 0 || (node.collapsedChildCount ?? 0) > 0}
          ontogglecollapse={toggleCollapse}
          {onselectsession}
        />
      </div>
    {/each}
  </div>
</div>

<style>
  .org-chart-scroll {
    flex: 1;
    overflow: hidden;
    background: hsl(var(--background));
    min-height: 0;
    position: relative;
    cursor: grab;
    user-select: none;
    isolation: isolate;
  }

  .org-chart-scroll.is-panning {
    cursor: grabbing;
  }

  .org-chart-canvas {
    position: relative;
    will-change: transform;
  }

  .org-chart-controls {
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 1;
    display: flex;
    gap: 4px;
  }

  .zoom-btn {
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid hsl(var(--border));
    border-radius: 6px;
    background: hsl(var(--card));
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    font-size: 16px;
    font-weight: 500;
    line-height: 1;
    padding: 0;
    transition: background 0.15s, color 0.15s;
  }

  .zoom-btn:hover {
    background: hsl(var(--accent));
    color: hsl(var(--accent-foreground));
  }
</style>
