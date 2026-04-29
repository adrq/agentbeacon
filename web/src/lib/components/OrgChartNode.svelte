<script lang="ts">
  import type { SessionSummary, SessionIdentity } from '../types';
  import AgentPill from './AgentPill.svelte';

  interface Props {
    session: SessionSummary;
    agentNameById: Map<string, string>;
    sessionIdentity?: Map<string, SessionIdentity>;
    now: number;
    selected?: boolean;
    collapsed?: boolean;
    collapsedChildCount?: number;
    hasChildren?: boolean;
    ontogglecollapse?: (sessionId: string) => void;
    onselectsession?: (sessionId: string) => void;
  }

  let {
    session, agentNameById, sessionIdentity, now,
    selected = false, collapsed = false, collapsedChildCount = 0,
    hasChildren = false, ontogglecollapse, onselectsession,
  }: Props = $props();

  let identity = $derived(sessionIdentity?.get(session.id));
  let agentFallback = $derived(agentNameById.get(session.agent_id) ?? session.agent_id.slice(0, 8));
  let slug = $derived(identity?.slug ?? agentFallback);
  let agentDisplayName = $derived(identity?.agentName ?? agentFallback);
  let role = $derived(identity?.role ?? '');

  const statusLabels: Record<string, string> = {
    'working': 'Working',
    'awaiting_input': 'Turn Complete',
    'idle': 'Idle',
    'stopped': 'Stopped',
    'unassigned': 'Unassigned',
    'crashed': 'Crashed',
    'completed': 'Completed',
    'failed': 'Failed',
    'canceled': 'Canceled',
  };

  let statusText = $derived(statusLabels[session.status] ?? session.status);
  let isTerminal = $derived(session.outcome != null);

  let elapsed = $derived(formatDuration(session.created_at, isTerminal ? session.updated_at : null, now));

  function formatDuration(startIso: string, endIso: string | null | undefined, nowMs: number): string {
    const start = new Date(startIso).getTime();
    const end = endIso ? new Date(endIso).getTime() : nowMs;
    const diff = Math.floor((end - start) / 1000);
    if (diff < 60) return `${diff}s`;
    const m = Math.floor(diff / 60);
    const s = diff % 60;
    if (m < 60) return `${m}m${s > 0 ? ` ${s}s` : ''}`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  let ariaLabel = $derived(
    `${slug}, ${statusText}${agentDisplayName ? `, ${agentDisplayName}` : ''}${role ? `, ${role}` : ''}`
  );
</script>

<div class="org-chart-node-wrapper">
  <button
    class="org-chart-node {session.status}"
    class:selected
    class:terminal={isTerminal}
    data-session-id={session.id}
    aria-label={ariaLabel}
    onclick={() => onselectsession?.(session.id)}
  >
    <div class="node-header">
      <span class="status-dot"></span>
      <span class="node-slug">{slug}</span>
    </div>
    <div class="node-meta">
      <AgentPill name={agentDisplayName} />
      {#if role}
        <span class="node-role">{role}</span>
      {/if}
    </div>
    <div class="node-footer">
      <span class="node-status-text">{statusText}</span>
      <span class="node-elapsed">{elapsed}</span>
    </div>
  </button>
  {#if hasChildren}
    <button
      class="collapse-toggle"
      class:collapsed
      aria-label={collapsed ? `Expand ${collapsedChildCount} children` : 'Collapse subtree'}
      onclick={(e) => { e.stopPropagation(); ontogglecollapse?.(session.id); }}
    >
      {#if collapsed}
        <span class="collapse-badge">+{collapsedChildCount}</span>
      {:else}
        <span class="collapse-chevron">▾</span>
      {/if}
    </button>
  {/if}
</div>

<style>
  .org-chart-node-wrapper {
    position: relative;
    width: 160px;
  }

  .org-chart-node {
    position: relative;
    width: 160px;
    height: 80px;
    box-sizing: border-box;
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    cursor: pointer;
    transition: background 0.15s;
    font-family: inherit;
    text-align: left;
    color: hsl(var(--foreground));
    outline: none;
  }

  .org-chart-node:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .org-chart-node:focus-visible {
    outline: 2px solid hsl(var(--primary));
    outline-offset: 2px;
  }

  .org-chart-node.selected {
    box-shadow: 0 0 0 2px hsl(var(--primary));
  }

  .org-chart-node.terminal {
    opacity: 0.7;
  }

  /* Status left border accents */
  .org-chart-node.working {
    border-left: 3px solid hsl(var(--status-working));
  }

  .org-chart-node.failed,
  .org-chart-node.crashed {
    border-left: 3px solid hsl(var(--status-danger));
    opacity: 1;
  }

  .node-header {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }

  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
    background: hsl(var(--muted-foreground));
  }

  .working .status-dot {
    background: hsl(var(--status-working));
    animation: pulse 2s ease-in-out infinite;
  }

  .completed .status-dot {
    background: hsl(var(--status-success));
  }

  .failed .status-dot,
  .crashed .status-dot {
    background: hsl(var(--status-danger));
  }

  .idle .status-dot {
    background: hsl(var(--status-attention));
  }

  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 2px 1px hsl(var(--status-working) / 0.15); }
    50% { box-shadow: 0 0 6px 2px hsl(var(--status-working) / 0.4); }
  }

  .node-slug {
    font-size: 0.875rem;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .node-meta {
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
  }

  .node-role {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .node-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
  }

  .node-status-text {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .node-elapsed {
    font-size: 0.6875rem;
    font-weight: 500;
    font-family: var(--font-mono, monospace);
    font-variant-numeric: tabular-nums;
    color: hsl(var(--muted-foreground));
  }

  .collapse-toggle {
    position: absolute;
    bottom: -12px;
    left: 50%;
    transform: translateX(-50%);
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: 8px;
    padding: 0 6px;
    height: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    z-index: 1;
    opacity: 0;
    transition: opacity 0.15s;
    font-family: inherit;
    color: hsl(var(--muted-foreground));
  }

  .collapse-toggle.collapsed {
    opacity: 1;
  }

  .org-chart-node-wrapper:hover .collapse-toggle {
    opacity: 1;
  }

  .collapse-badge {
    font-size: 0.625rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: hsl(var(--muted-foreground));
  }

  .collapse-chevron {
    font-size: 0.5rem;
    line-height: 1;
    color: hsl(var(--muted-foreground));
  }
</style>
