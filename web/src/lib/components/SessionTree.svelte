<script lang="ts">
  import { tick } from 'svelte';
  import type { SessionSummary, Agent } from '../types';
  import { api } from '../api';
  import { toasts } from '../stores/toasts';

  interface Props {
    sessions: SessionSummary[];
    agents: Agent[];
    selectedSessionId?: string | null;
    isTerminal?: boolean;
    onselectsession?: (sessionId: string | null) => void;
    onstatuschange?: () => void;
  }

  let { sessions, agents, selectedSessionId = null, isTerminal = false, onselectsession, onstatuschange }: Props = $props();

  const TERMINAL_STATUSES = new Set(['completed', 'failed', 'canceled']);

  async function handleCancel(e: Event, sessionId: string) {
    e.stopPropagation();
    try {
      await api.cancelSession(sessionId);
      onstatuschange?.();
    } catch (err) {
      console.error('Failed to cancel session:', err);
      toasts.error(`Failed to cancel session: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  }

  async function handleComplete(e: Event, sessionId: string) {
    e.stopPropagation();
    try {
      await api.completeSession(sessionId);
      onstatuschange?.();
    } catch (err) {
      console.error('Failed to complete session:', err);
      toasts.error(`Failed to complete session: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  }

  async function handleRecover(e: Event, sessionId: string) {
    e.stopPropagation();
    try {
      await api.recoverSession(sessionId);
      onstatuschange?.();
    } catch (err) {
      console.error('Failed to recover session:', err);
      toasts.error(`Failed to recover session: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  }

  function isNonTerminal(status: string): boolean {
    return !TERMINAL_STATUSES.has(status);
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

  // --- New state for disclosure + auto-collapse ---

  let treeOpen = $state(true);
  let userToggledTree = false;
  let manuallyExpanded = $state<Set<string>>(new Set());
  let treeContainer: HTMLDivElement | undefined = $state();

  // Reset state when switching executions
  let prevExecutionId = '';
  $effect.pre(() => {
    const execId = sessions[0]?.execution_id ?? '';
    if (execId !== prevExecutionId) {
      prevExecutionId = execId;
      manuallyExpanded = new Set();
      userToggledTree = false;
      treeOpen = !isTerminal;
    }
  });

  // Auto-close when execution becomes terminal during live viewing
  $effect(() => {
    if (isTerminal && !userToggledTree) {
      treeOpen = false;
    }
  });

  // Derived computations
  let activeCount = $derived(sessions.filter(s => !TERMINAL_STATUSES.has(s.status)).length);
  let totalCount = $derived(sessions.length);
  let tree = $derived(buildTree(sessions));

  // Scroll selected session into view (depends on treeOpen so it re-triggers when tree opens)
  $effect(() => {
    const open = treeOpen;
    const sid = selectedSessionId;
    if (!open || !sid || !treeContainer) return;
    tick().then(() => {
      const el = treeContainer?.querySelector(`[data-session-id="${CSS.escape(sid)}"]`);
      el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
  });

  // --- Auto-collapse helpers ---

  function partitionChildren(children: TreeNode[]): { active: TreeNode[]; terminal: TreeNode[] } {
    const active: TreeNode[] = [];
    const terminal: TreeNode[] = [];
    for (const child of children) {
      if (TERMINAL_STATUSES.has(child.session.status)) {
        terminal.push(child);
      } else {
        active.push(child);
      }
    }
    return { active, terminal };
  }

  function terminalSummaryText(nodes: TreeNode[]): string {
    const counts: Record<string, number> = {};
    for (const n of nodes) {
      counts[n.session.status] = (counts[n.session.status] ?? 0) + 1;
    }
    const order = ['completed', 'failed', 'canceled'];
    return order
      .filter(s => counts[s])
      .map(s => `${counts[s]} ${s}`)
      .join(', ');
  }

  function containsSession(nodes: TreeNode[], id: string | null | undefined): boolean {
    if (!id) return false;
    return nodes.some(n => n.session.id === id || containsSession(n.children, id));
  }

  function toggleExpand(parentId: string) {
    const next = new Set(manuallyExpanded);
    if (next.has(parentId)) {
      next.delete(parentId);
    } else {
      next.add(parentId);
    }
    manuallyExpanded = next;
  }
</script>

{#snippet renderNode(node: TreeNode, depth: number)}
  {@const s = node.session}
  {@const { active, terminal } = partitionChildren(node.children)}
  {@const isExpanded = manuallyExpanded.has(s.id) || containsSession(terminal, selectedSessionId)}

  <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
  <div
    class="tree-node {s.status}"
    class:active={selectedSessionId === s.id}
    style="padding-left: {1 + depth}rem"
    data-session-id={s.id}
    onclick={() => handleClick(s.id)}
  >
    {#if depth > 0}
      <span class="tree-branch">└─</span>
    {/if}
    {#if terminal.length > 0}
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
      <span class="node-chevron" class:expanded={isExpanded}
        onclick={(e) => { e.stopPropagation(); toggleExpand(s.id); }}>&#x25B8;</span>
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
    {#if s.status === 'failed' && s.agent_session_id}
      <button class="action-btn recover-btn" title="Attempt recovery" onclick={(e) => handleRecover(e, s.id)}>
        &#x21BB;
      </button>
    {/if}
  </div>

  <!-- Active children: always rendered -->
  {#each active as child}
    {@render renderNode(child, depth + 1)}
  {/each}

  <!-- Terminal children: collapsed summary or expanded -->
  {#if terminal.length > 0}
    {#if isExpanded}
      {#each terminal as child}
        {@render renderNode(child, depth + 1)}
      {/each}
    {:else}
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
      <div
        class="terminal-summary"
        style="padding-left: {1 + (depth + 1) * 1}rem"
        onclick={(e) => { e.stopPropagation(); toggleExpand(s.id); }}
      >
        <span class="tree-branch">└─</span>
        <span class="summary-text">{terminalSummaryText(terminal)}</span>
      </div>
    {/if}
  {/if}
{/snippet}

<div class="session-tree">
  <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
  <div class="tree-disclosure" onclick={() => { userToggledTree = true; treeOpen = !treeOpen; }}>
    <span class="disclosure-chevron" class:open={treeOpen}>&#x25B8;</span>
    <span class="disclosure-label">Sessions</span>
    <span class="disclosure-counts">
      {#if activeCount > 0}
        <span class="count-active">{activeCount} active</span>
      {/if}
      {#if activeCount > 0 && totalCount > activeCount}
        <span class="count-sep">&middot;</span>
      {/if}
      <span class="count-total">{totalCount} total</span>
    </span>
  </div>

  {#if treeOpen}
    <div class="tree-body scroll-thin" bind:this={treeContainer}>
      {#each tree as node}
        {@render renderNode(node, 0)}
      {/each}
    </div>
  {/if}
</div>

<style>
  .session-tree {
    padding: 0.5rem 0;
    flex-shrink: 0;
  }

  .tree-disclosure {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    width: 100%;
    padding: 0.25rem 1rem;
    cursor: pointer;
    font-size: 0.6875rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: hsl(var(--muted-foreground));
    transition: background 0.1s;
  }

  .tree-disclosure:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .disclosure-chevron {
    font-size: 0.5rem;
    transition: transform 0.15s ease;
    display: inline-block;
  }

  .disclosure-chevron.open {
    transform: rotate(90deg);
  }

  .disclosure-counts {
    margin-left: auto;
    font-weight: 500;
    text-transform: none;
    letter-spacing: normal;
  }

  .count-active {
    color: hsl(var(--status-working));
  }

  .count-total {
    color: hsl(var(--muted-foreground));
  }

  .count-sep {
    opacity: 0.4;
    margin: 0 0.25rem;
  }

  .tree-body {
    max-height: clamp(120px, 20vh, 300px);
    overflow-y: auto;
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
    font-size: 0.6875rem;
    flex-shrink: 0;
  }

  .node-icon {
    width: 0.875rem;
    text-align: center;
    flex-shrink: 0;
    font-size: 0.6875rem;
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

  .node-chevron {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 0.875rem;
    height: 0.875rem;
    cursor: pointer;
    font-size: 0.5rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
    transition: transform 0.15s ease;
  }

  .node-chevron.expanded {
    transform: rotate(90deg);
  }

  .node-chevron:hover {
    color: hsl(var(--foreground));
  }

  .terminal-summary {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.25rem 1rem;
    cursor: pointer;
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
    font-style: italic;
    transition: background 0.1s;
  }

  .terminal-summary:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.25rem;
    height: 1.25rem;
    border: none;
    border-radius: var(--radius-sm);
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

  .recover-btn:hover {
    color: hsl(var(--status-working));
    background: hsl(var(--status-working) / 0.1);
  }
</style>
