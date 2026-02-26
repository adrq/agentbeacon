<script lang="ts">
  import type { SessionSummary, Agent } from '../types';
  import { api } from '../api';

  interface Props {
    sessions: SessionSummary[];
    agents: Agent[];
    selectedSessionId?: string | null;
    onselectsession?: (sessionId: string | null) => void;
    onstatuschange?: () => void;
  }

  let { sessions, agents, selectedSessionId = null, onselectsession, onstatuschange }: Props = $props();

  async function handleCancel(e: Event, sessionId: string) {
    e.stopPropagation();
    try {
      await api.cancelSession(sessionId);
      onstatuschange?.();
    } catch (err) {
      console.error('Failed to cancel session:', err);
    }
  }

  async function handleComplete(e: Event, sessionId: string) {
    e.stopPropagation();
    try {
      await api.completeSession(sessionId);
      onstatuschange?.();
    } catch (err) {
      console.error('Failed to complete session:', err);
    }
  }

  function isNonTerminal(status: string): boolean {
    return !['completed', 'failed', 'canceled'].includes(status);
  }

  interface TreeNode {
    session: SessionSummary;
    children: TreeNode[];
  }

  function buildTree(sessions: SessionSummary[]): TreeNode[] {
    const byId = new Map<string, TreeNode>();
    const roots: TreeNode[] = [];

    for (const s of sessions) {
      byId.set(s.id, { session: s, children: [] });
    }

    for (const s of sessions) {
      const node = byId.get(s.id)!;
      if (s.parent_session_id && byId.has(s.parent_session_id)) {
        byId.get(s.parent_session_id)!.children.push(node);
      } else {
        roots.push(node);
      }
    }

    return roots;
  }

  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
  }

  function statusIcon(status: string): string {
    switch (status) {
      case 'working': return '\u25CF';      // ●
      case 'completed': return '\u2713';     // ✓
      case 'input-required': return '!';
      case 'failed': return '\u2717';        // ✗
      case 'submitted': return '\u25CB';     // ○
      case 'canceled': return '\u25CB';
      default: return '\u25CB';
    }
  }

  function handleClick(sessionId: string) {
    const next = selectedSessionId === sessionId ? null : sessionId;
    onselectsession?.(next);
  }

  let tree = $derived(buildTree(sessions));
</script>

{#snippet renderNode(node: TreeNode, depth: number)}
  {@const s = node.session}
  <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
  <div
    class="tree-node {s.status}"
    class:active={selectedSessionId === s.id}
    style="padding-left: {1 + depth * 1}rem"
    onclick={() => handleClick(s.id)}
  >
    {#if depth > 0}
      <span class="tree-branch">&boxur;&horz;</span>
    {/if}
    <span class="node-icon">{statusIcon(s.status)}</span>
    <span class="node-label">{depth === 0 ? `Lead (${agentName(s.agent_id)})` : agentName(s.agent_id)}</span>
    <span class="node-status">{s.status}</span>
    {#if isNonTerminal(s.status)}
      <button class="action-btn cancel-btn" title="Cancel session" onclick={(e) => handleCancel(e, s.id)}>
        &#x2717;
      </button>
    {/if}
    {#if s.status === 'input-required'}
      <button class="action-btn complete-btn" title="Complete session" onclick={(e) => handleComplete(e, s.id)}>
        &#x2713;
      </button>
    {/if}
  </div>
  {#each node.children as child}
    {@render renderNode(child, depth + 1)}
  {/each}
{/snippet}

<div class="session-tree">
  <div class="tree-header">Sessions</div>
  {#each tree as node}
    {@render renderNode(node, 0)}
  {/each}
</div>

<style>
  .session-tree {
    padding: 0.5rem 0;
  }

  .tree-header {
    font-size: 0.6875rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: hsl(var(--muted-foreground));
    padding: 0 1rem 0.375rem;
  }

  .tree-node {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    width: 100%;
    text-align: left;
    padding: 0.25rem 1rem;
    border: none;
    background: transparent;
    cursor: pointer;
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
    transition: background 0.1s;
  }

  .tree-node:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .tree-node.active {
    background: hsl(var(--muted));
  }

  .tree-branch {
    color: hsl(var(--muted-foreground));
    font-size: 0.75rem;
    flex-shrink: 0;
  }

  .node-icon {
    width: 0.875rem;
    text-align: center;
    flex-shrink: 0;
    font-size: 0.75rem;
    font-weight: 700;
  }

  .working .node-icon { color: hsl(var(--status-working)); }
  .completed .node-icon { color: hsl(var(--status-success)); }
  .input-required .node-icon { color: hsl(var(--status-attention)); }
  .failed .node-icon { color: hsl(var(--status-danger)); }
  .submitted .node-icon, .canceled .node-icon { color: hsl(var(--muted-foreground)); }

  .node-label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .node-status {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.25rem;
    height: 1.25rem;
    border: none;
    border-radius: 0.25rem;
    background: transparent;
    cursor: pointer;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
    opacity: 0;
    transition: opacity 0.1s, color 0.1s, background 0.1s;
  }

  .tree-node:hover .action-btn {
    opacity: 1;
  }

  .cancel-btn:hover {
    color: hsl(var(--status-danger));
    background: hsl(var(--status-danger) / 0.1);
  }

  .complete-btn:hover {
    color: hsl(var(--status-success));
    background: hsl(var(--status-success) / 0.1);
  }
</style>
