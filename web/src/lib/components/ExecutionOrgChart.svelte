<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { SessionSummary, Agent, SessionIdentity } from '../types';
  import { buildTree, applyCompaction, computeLayout, edgePath } from '../utils/treeLayout';
  import OrgChartNode from './OrgChartNode.svelte';
  import OrgChartGroupNode from './OrgChartGroupNode.svelte';
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

  let tree = $derived(buildTree(sessions));
  let compacted = $derived(applyCompaction(tree));
  let layout = $derived(computeLayout(compacted));

  const PADDING = 40;
  let canvasWidth = $derived(layout.bounds.width + PADDING * 2);
  let canvasHeight = $derived(layout.bounds.height + PADDING * 2);
</script>

<div class="org-chart-scroll" id="execution-overview">
  <div
    class="org-chart-canvas"
    style="width: {canvasWidth}px; height: {canvasHeight}px;"
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

    {#each layout.nodes as node (node.type === 'session' ? node.session?.id : `group-${node.groupedSessions?.map(s => s.id).join('-')}`)}
      {#if node.type === 'session' && node.session}
        <div style="position: absolute; left: {node.x + PADDING}px; top: {node.y + PADDING}px;">
          <OrgChartNode
            session={node.session}
            {agentNameById}
            {sessionIdentity}
            {now}
            selected={$selectedSessionId === node.session.id}
            {onselectsession}
          />
        </div>
      {:else if node.type === 'group' && node.groupedSessions}
        <div style="position: absolute; left: {node.x + PADDING}px; top: {node.y + PADDING}px;">
          <OrgChartGroupNode
            nodes={node.groupedSessions}
            {sessionIdentity}
            {now}
            {onselectsession}
          />
        </div>
      {/if}
    {/each}
  </div>
</div>

<style>
  .org-chart-scroll {
    flex: 1;
    overflow: auto;
    background: hsl(var(--background));
    min-height: 0;
  }

  .org-chart-canvas {
    position: relative;
    margin: 0 auto;
    min-width: fit-content;
  }
</style>
