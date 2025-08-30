<script lang="ts">
  export let workflow: string = '';
  export let isExecuting: boolean = false;

  // Parse YAML workflow to extract nodes and dependencies
  let nodes: Array<{ id: string; status: string; position: { x: number; y: number } }> = [];
  let edges: Array<{ from: string; to: string }> = [];

  $: {
    // Simple YAML parsing for demo - in production would use proper YAML parser
    parseWorkflow(workflow);
  }

  function parseWorkflow(yamlContent: string) {
    nodes = [];
    edges = [];

    if (!yamlContent.trim()) return;

    // Extract node IDs and dependencies from YAML (basic regex parsing)
    const nodeMatches = yamlContent.matchAll(/- id:\s*(\w+)/g);
    const dependencyMatches = yamlContent.matchAll(/depends_on:\s*\[(.*?)\]/g);

    const nodeIds = Array.from(nodeMatches).map(match => match[1]);
    const dependencies = Array.from(dependencyMatches).map(match =>
      match[1].split(',').map(dep => dep.trim().replace(/['"]/g, ''))
    );

    // Create nodes with positions
    nodeIds.forEach((id, index) => {
      const col = index % 3;
      const row = Math.floor(index / 3);
      nodes.push({
        id,
        status: isExecuting ? (index === 0 ? 'running' : 'pending') : 'idle',
        position: { x: col * 200 + 100, y: row * 150 + 100 }
      });
    });

    // Create edges from dependencies
    dependencies.forEach((deps, index) => {
      const targetNode = nodeIds[index + 1]; // Assuming dependencies are for next node
      if (targetNode) {
        deps.forEach(dep => {
          if (nodeIds.includes(dep)) {
            edges.push({ from: dep, to: targetNode });
          }
        });
      }
    });
  }

  function getNodeColor(status: string): string {
    switch (status) {
      case 'running': return '#3b82f6';
      case 'completed': return '#10b981';
      case 'failed': return '#ef4444';
      case 'pending': return '#f59e0b';
      default: return '#6b7280';
    }
  }
</script>

<div class="dag-container">
  {#if nodes.length === 0}
    <div class="empty-state">
      <div class="empty-icon">📊</div>
      <h3>No Workflow Loaded</h3>
      <p>Enter a workflow in the editor to see the DAG visualization</p>
    </div>
  {:else}
    <svg class="dag-svg" viewBox="0 0 800 600">
      <!-- Edges (drawn first so they appear behind nodes) -->
      {#each edges as edge}
        {@const fromNode = nodes.find(n => n.id === edge.from)}
        {@const toNode = nodes.find(n => n.id === edge.to)}
        {#if fromNode && toNode}
          <line
            x1={fromNode.position.x}
            y1={fromNode.position.y}
            x2={toNode.position.x}
            y2={toNode.position.y}
            stroke="#6b7280"
            stroke-width="2"
            marker-end="url(#arrowhead)"
          />
        {/if}
      {/each}

      <!-- Arrow marker definition -->
      <defs>
        <marker
          id="arrowhead"
          markerWidth="10"
          markerHeight="7"
          refX="9"
          refY="3.5"
          orient="auto"
        >
          <polygon
            points="0 0, 10 3.5, 0 7"
            fill="#6b7280"
          />
        </marker>
      </defs>

      <!-- Nodes -->
      {#each nodes as node}
        <g class="node-group">
          <!-- Node circle -->
          <circle
            cx={node.position.x}
            cy={node.position.y}
            r="30"
            fill={getNodeColor(node.status)}
            stroke="#ffffff"
            stroke-width="3"
            class="node-circle"
          />

          <!-- Node label -->
          <text
            x={node.position.x}
            y={node.position.y - 45}
            text-anchor="middle"
            class="node-label"
            fill="var(--text-color)"
          >
            {node.id}
          </text>

          <!-- Status indicator -->
          {#if node.status === 'running'}
            <circle
              cx={node.position.x}
              cy={node.position.y}
              r="20"
              fill="none"
              stroke="#ffffff"
              stroke-width="2"
              class="running-indicator"
            />
          {/if}
        </g>
      {/each}
    </svg>

    <!-- Legend -->
    <div class="legend">
      <div class="legend-item">
        <div class="legend-color" style="background-color: #6b7280;"></div>
        <span>Idle</span>
      </div>
      <div class="legend-item">
        <div class="legend-color" style="background-color: #f59e0b;"></div>
        <span>Pending</span>
      </div>
      <div class="legend-item">
        <div class="legend-color" style="background-color: #3b82f6;"></div>
        <span>Running</span>
      </div>
      <div class="legend-item">
        <div class="legend-color" style="background-color: #10b981;"></div>
        <span>Completed</span>
      </div>
      <div class="legend-item">
        <div class="legend-color" style="background-color: #ef4444;"></div>
        <span>Failed</span>
      </div>
    </div>
  {/if}
</div>

<style>
  .dag-container {
    height: 100%;
    display: flex;
    flex-direction: column;
    position: relative;
  }

  .empty-state {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--text-secondary);
  }

  .empty-icon {
    font-size: 3rem;
    margin-bottom: 1rem;
  }

  .empty-state h3 {
    margin: 0 0 0.5rem 0;
    font-size: 1.25rem;
    color: var(--text-primary);
  }

  .empty-state p {
    margin: 0;
    font-size: 0.875rem;
  }

  .dag-svg {
    flex: 1;
    width: 100%;
    height: 100%;
    border: 1px solid var(--border-color);
    border-radius: 0.25rem;
    background: var(--panel-bg);
  }

  .node-group {
    cursor: pointer;
  }

  .node-circle {
    transition: r 0.2s ease;
  }

  .node-group:hover .node-circle {
    r: 35;
  }

  .node-label {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 0.75rem;
    font-weight: 600;
    pointer-events: none;
  }

  .running-indicator {
    animation: pulse 1.5s ease-in-out infinite;
  }

  .legend {
    display: flex;
    gap: 1rem;
    padding: 1rem 0 0.5rem 0;
    border-top: 1px solid var(--border-color);
    margin-top: 1rem;
  }

  .legend-item {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.75rem;
    color: var(--text-secondary);
  }

  .legend-color {
    width: 12px;
    height: 12px;
    border-radius: 50%;
  }

  @keyframes pulse {
    0% {
      opacity: 1;
      r: 20;
    }
    50% {
      opacity: 0.5;
      r: 25;
    }
    100% {
      opacity: 1;
      r: 20;
    }
  }
</style>
