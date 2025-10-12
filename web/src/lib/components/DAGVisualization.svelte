<script lang="ts">
  import { onMount } from 'svelte';
  import { writable } from 'svelte/store';
  import { SvelteFlow, Controls, Background, MiniMap } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import ELK from 'elkjs/lib/elk.bundled.js';
  import yaml from 'js-yaml';
  import type { NodeStatus } from '../types';

  export let workflow: string = '';
  export let isValid: boolean = false;
  export let theme: 'dark' | 'light' = 'dark';
  export let executionState: NodeStatus | undefined = undefined;
  export let placeholderMode: boolean = false;

  interface DAGNode {
    id: string;
    data: {
      label: string;
      agent: string;
      status?: string;
    };
    position: { x: number; y: number };
  }

  interface DAGEdge {
    id: string;
    source: string;
    target: string;
    type: string;
  }

  let nodes = writable<DAGNode[]>([]);
  let edges = writable<DAGEdge[]>([]);
  let lastValidLayout: { nodes: DAGNode[]; edges: DAGEdge[] } | null = null;

  const elk = new ELK();

  // Parse workflow and compute ELK layout
  async function parseAndLayoutWorkflow(yamlContent: string) {
    if (!yamlContent.trim()) {
      nodes.set([]);
      edges.set([]);
      return;
    }

    try {
      // Parse YAML
      const workflowData: any = yaml.load(yamlContent);

      if (!workflowData || !workflowData.tasks) {
        throw new Error('Invalid workflow: missing tasks');
      }

      // Build nodes and edges with status if executionState provided
      const nodeList = workflowData.tasks.map((node: any, index: number) => {
        const status = executionState?.[node.id];
        const statusIcon = status === 'completed' ? '✅' :
                           status === 'running' ? '🔄' :
                           status === 'waiting' ? '⏸️' :
                           status === 'failed' ? '❌' : '';

        return {
          id: node.id,
          data: {
            label: statusIcon ? `${statusIcon} ${node.id}` : node.id,
            agent: node.agent || 'unknown',
            status: status
          },
          position: { x: 0, y: 0 }, // Will be updated by ELK
        };
      });

      const edgeList: any[] = [];
      workflowData.tasks.forEach((node: any) => {
        if (node.depends_on && Array.isArray(node.depends_on)) {
          node.depends_on.forEach((dep: string) => {
            edgeList.push({
              id: `${dep}->${node.id}`,
              source: dep,
              target: node.id,
              type: 'default',
            });
          });
        }
      });

      // Run ELK layout algorithm
      const elkGraph = {
        id: 'root',
        layoutOptions: {
          'elk.algorithm': 'layered',
          'elk.direction': 'DOWN',
          'elk.spacing.nodeNode': '50',
          'elk.layered.spacing.nodeNodeBetweenLayers': '100',
          'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
        },
        children: nodeList.map((node: DAGNode) => ({
          id: node.id,
          width: 150,
          height: 50,
        })),
        edges: edgeList.map((edge) => ({
          id: edge.id,
          sources: [edge.source],
          targets: [edge.target],
        })),
      };

      const layout = await elk.layout(elkGraph);

      // Update node positions from ELK layout
      const positionedNodes = nodeList.map((node: DAGNode) => {
        const elkNode = layout.children?.find((n) => n.id === node.id);
        return {
          ...node,
          position: elkNode ? { x: elkNode.x || 0, y: elkNode.y || 0 } : node.position,
        };
      });

      // Store as last valid layout
      lastValidLayout = { nodes: positionedNodes, edges: edgeList };

      nodes.set(positionedNodes);
      edges.set(edgeList);
    } catch (error) {
      console.error('Error parsing workflow:', error);
      // On error, preserve last valid layout if available
      if (lastValidLayout) {
        nodes.set(lastValidLayout.nodes);
        edges.set(lastValidLayout.edges);
      }
    }
  }

  // Placeholder mode: show static DAG without parsing
  function showPlaceholderDAG() {
    const placeholderNodes = [
      { id: 'analyze', data: { label: 'analyze', agent: 'claude-code' }, position: { x: 100, y: 50 } },
      { id: 'fix_tests', data: { label: 'fix_tests', agent: 'claude-code' }, position: { x: 100, y: 150 } },
      { id: 'validate', data: { label: 'validate', agent: 'cursor' }, position: { x: 100, y: 250 } },
    ];
    const placeholderEdges = [
      { id: 'analyze->fix_tests', source: 'analyze', target: 'fix_tests', type: 'default' },
      { id: 'fix_tests->validate', source: 'fix_tests', target: 'validate', type: 'default' },
    ];
    nodes.set(placeholderNodes);
    edges.set(placeholderEdges);
  }

  // Only update DAG when isValid is true - use separate reactive blocks for each prop
  $: {
    if (placeholderMode) {
      showPlaceholderDAG();
    } else if (isValid && workflow) {
      parseAndLayoutWorkflow(workflow);
    }
  }

  // Re-render with execution state changes (for live progress updates)
  $: if (executionState && workflow && isValid) {
    parseAndLayoutWorkflow(workflow);
  }

  // Preserve frozen state when isValid becomes false
  $: if (!isValid && !placeholderMode && lastValidLayout) {
    nodes.set(lastValidLayout.nodes);
    edges.set(lastValidLayout.edges);
  }
</script>

<div class="dag-container" class:dark={theme === 'dark'}>
  {#if $nodes.length === 0}
    <div class="empty-state">
      <div class="empty-icon">📊</div>
      <h3>No Workflow Loaded</h3>
      <p>Click "Load Sample" or enter a workflow in the editor, then click "Validate Workflow" to see the DAG visualization</p>
    </div>
  {:else}
    <SvelteFlow nodes={$nodes} edges={$edges} fitView>
      <Controls />
      <Background
        bgColor={theme === 'dark' ? '#0f172a' : '#ffffff'}
        gap={16}
      />
      <MiniMap />
    </SvelteFlow>
  {/if}
</div>

<style>
  .dag-container {
    height: 100%;
    width: 100%;
    position: relative;
    background: var(--panel-bg);
    border: 1px solid var(--border-color);
    border-radius: 0.25rem;
  }

  .dag-container.dark {
    --panel-bg: #0f172a;
    --border-color: #334155;
  }

  .empty-state {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    color: var(--text-secondary);
    padding: 2rem;
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
    max-width: 400px;
  }

  /* Svelte Flow theming */
  .dag-container :global(.svelte-flow) {
    background: #ffffff;
  }

  .dag-container.dark :global(.svelte-flow) {
    background: #0f172a !important;
  }

  /* Dark mode background pattern */
  .dag-container.dark :global(.svelte-flow__background) {
    background-color: #0f172a !important;
  }

  .dag-container.dark :global(.svelte-flow__background pattern circle) {
    fill: #475569 !important;
  }

  /* Dark mode nodes */
  .dag-container.dark :global(.svelte-flow__node) {
    background: #1e293b;
    border: 2px solid #475569;
    color: #e2e8f0;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  }

  .dag-container.dark :global(.svelte-flow__node:hover) {
    border-color: #64748b;
    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
  }

  .dag-container.dark :global(.svelte-flow__node.selected) {
    border-color: #3b82f6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.3);
  }

  /* Dark mode edges */
  .dag-container.dark :global(.svelte-flow__edge-path) {
    stroke: #64748b;
    stroke-width: 2;
  }

  .dag-container.dark :global(.svelte-flow__edge.selected .svelte-flow__edge-path) {
    stroke: #3b82f6;
  }

  /* Dark mode controls */
  .dag-container.dark :global(.svelte-flow__controls) {
    background: #1e293b;
    border: 1px solid #475569;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  }

  .dag-container.dark :global(.svelte-flow__controls button) {
    background: #334155;
    border-bottom: 1px solid #475569;
    color: #e2e8f0;
  }

  .dag-container.dark :global(.svelte-flow__controls button:hover) {
    background: #475569;
  }

  .dag-container.dark :global(.svelte-flow__controls button:disabled) {
    background: #1e293b;
    color: #64748b;
  }

  /* Dark mode minimap */
  .dag-container.dark :global(.svelte-flow__minimap) {
    background: #1e293b;
    border: 1px solid #475569;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
  }

  .dag-container.dark :global(.svelte-flow__minimap-mask) {
    fill: rgba(15, 23, 42, 0.6);
  }

  .dag-container.dark :global(.svelte-flow__minimap-node) {
    fill: #334155;
    stroke: #475569;
  }

  /* Dark mode attribution link */
  .dag-container.dark :global(.svelte-flow__attribution) {
    background: rgba(30, 41, 59, 0.8);
    color: #94a3b8;
  }

  .dag-container.dark :global(.svelte-flow__attribution a) {
    color: #60a5fa;
  }
</style>
