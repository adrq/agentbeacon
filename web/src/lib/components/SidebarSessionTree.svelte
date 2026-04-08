<script lang="ts">
  import type { SessionSummary, Agent, UsageState, WorktreeInfo, SessionIdentity } from '../types';
  import { buildTree, partitionChildren, terminalSummaryText, containsSession, type TreeNode } from '../utils/treeLayout';
  import AgentPill from './AgentPill.svelte';
  import { formatTokens } from '../format';
  import { api } from '../api';
  import { toasts } from '../stores/toasts';
  import CopyButton from './CopyButton.svelte';

  interface Props {
    sessions: SessionSummary[];
    agents: Agent[];
    selectedSessionId?: string | null;
    isTerminal?: boolean;
    usageBySession?: Map<string, UsageState>;
    poolAgents?: { agent_id: string; name: string }[];
    maxDepth?: number;
    maxWidth?: number;
    sessionIdentity?: Map<string, SessionIdentity>;
    onselectsession?: (sessionId: string | null) => void;
    onstatuschange?: () => void;
  }

  let { sessions, agents, selectedSessionId = null, isTerminal = false, usageBySession, poolAgents, maxDepth, maxWidth, sessionIdentity, onselectsession, onstatuschange }: Props = $props();

  async function handleTerminate(e: Event, sessionId: string) {
    e.stopPropagation();
    try {
      await api.terminateSession(sessionId);
      onstatuschange?.();
    } catch (err) {
      toasts.error(`Failed to terminate session: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  }

  async function handleRecover(e: Event, sessionId: string) {
    e.stopPropagation();
    try {
      await api.recoverSession(sessionId);
      onstatuschange?.();
    } catch (err) {
      toasts.error(`Failed to recover session: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  }


  function agentName(agentId: string): string {
    const agent = agents.find(a => a.id === agentId);
    return agent?.name ?? agentId.slice(0, 8);
  }

  function statusIcon(status: string): string {
    switch (status) {
      case 'working': return '\u25CF';
      case 'idle': return '\u2758\u2758';
      case 'stopped': return '\u25A0';
      case 'unassigned': return '\u25CB';
      case 'crashed': return '\u26A0';
      case 'completed': return '\u2713';
      case 'failed': return '\u2717';
      case 'canceled': return '\u25CB';
      default: return '\u25CB';
    }
  }

  function handleClick(sessionId: string) {
    const next = selectedSessionId === sessionId ? null : sessionId;
    onselectsession?.(next);
  }

  let manuallyExpanded = $state<Set<string>>(new Set());

  let prevExecutionId = '';
  $effect.pre(() => {
    const execId = sessions[0]?.execution_id ?? '';
    if (execId !== prevExecutionId) {
      prevExecutionId = execId;
      manuallyExpanded = new Set();
    }
  });

  function toggleExpand(parentId: string) {
    const next = new Set(manuallyExpanded);
    if (next.has(parentId)) {
      next.delete(parentId);
    } else {
      next.add(parentId);
    }
    manuallyExpanded = next;
  }

  let tree = $derived(buildTree(sessions));
  let leadSession = $derived(sessions.find(s => !s.parent_session_id) ?? null);

  function formatDuration(startIso: string, endIso?: string | null): string {
    const start = new Date(startIso).getTime();
    const end = endIso ? new Date(endIso).getTime() : Date.now();
    const diff = Math.floor((end - start) / 1000);
    if (diff < 60) return `${diff}s`;
    const m = Math.floor(diff / 60);
    const s = diff % 60;
    if (m < 60) return `${m}m${s > 0 ? ` ${s}s` : ''}`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  let execMetaBase = $derived(() => {
    if (!leadSession) return '';
    const identity = sessionIdentity?.get(leadSession.id);
    const name = identity?.slug ?? agentName(leadSession.agent_id);
    const d = maxDepth ?? 0;
    const w = maxWidth ?? sessions.length;
    return `${name} · D:${d} W:${w}`;
  });

  let worktreePath = $derived(leadSession?.worktree_path ?? null);

  let worktreeInfo = $state<WorktreeInfo | null>(null);

  $effect(() => {
    const id = leadSession?.id;
    if (!id) { worktreeInfo = null; return; }
    api.getSessionWorktree(id)
      .then(info => { if (leadSession?.id === id) worktreeInfo = info; })
      .catch(() => { if (leadSession?.id === id) worktreeInfo = null; });
  });

  let branchName = $derived(worktreeInfo?.branch ?? null);
  let worktreeExists = $derived(worktreeInfo?.exists ?? false);
</script>

{#snippet renderNode(node: TreeNode, depth: number)}
  {@const s = node.session}
  {@const { active, terminal } = partitionChildren(node.children)}
  {@const isExpanded = manuallyExpanded.has(s.id) || containsSession(terminal, selectedSessionId)}
  {@const displayDepth = Math.min(depth, 4)}
  {@const isFlattened = depth > 4}
  {@const usage = usageBySession?.get(s.id)}
  {@const usagePct = usage?.available && (usage?.contextWindow ?? 0) > 0
    ? Math.max(0, Math.min(100, Math.round(100 * (usage?.inputTokens ?? 0) / (usage?.contextWindow ?? 1))))
    : null}
  {@const usageLevel = usagePct !== null ? (usagePct >= 90 ? 'danger' : usagePct >= 70 ? 'warning' : 'ok') : null}
  {@const usageTitle = usagePct !== null && usage
    ? `${formatTokens(usage.inputTokens)} / ${formatTokens(usage.contextWindow)} (${usagePct}%)`
    : usage && !usage.available ? 'Context tracking unavailable' : ''}
  {@const identity = sessionIdentity?.get(s.id)}

  <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
  <div
    class="sidebar-node {s.status}"
    class:active={selectedSessionId === s.id}
    style="padding-left: {0.5 + displayDepth * 0.75}rem"
    data-session-id={s.id}
    onclick={() => handleClick(s.id)}
    title={usageTitle || undefined}
  >
    {#if isFlattened}
      <span class="depth-badge">{depth}</span>
    {:else if depth > 0}
      <span class="tree-branch">└</span>
    {/if}
    {#if terminal.length > 0}
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
      <span class="node-chevron" class:expanded={isExpanded}
        onclick={(e) => { e.stopPropagation(); toggleExpand(s.id); }}>&#x25B8;</span>
    {/if}
    <span class="node-icon">{statusIcon(s.status)}</span>
    <span class="node-label">
      <span class="node-slug">{identity?.slug ?? agentName(s.agent_id)}</span>
      {#if identity?.agentName}
        <AgentPill name={identity.agentName} />
      {/if}
    </span>
    <span class="node-status">{s.status}</span>
    {#if usagePct !== null}
      <span
        class="context-bar"
        role="meter"
        aria-label="Context: {usagePct}%"
        aria-valuenow={usagePct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span class="context-fill {usageLevel}" style="width: {usagePct}%"></span>
      </span>
    {:else if usage && !usage.available}
      <span class="context-bar unavailable"><span class="context-dash">&mdash;</span></span>
    {:else}
      <span class="context-bar placeholder" aria-hidden="true"></span>
    {/if}
    <span class="action-zone">
      {#if s.outcome == null}
        <button class="action-btn cancel-btn" title="Terminate session" onclick={(e) => handleTerminate(e, s.id)}>
          &#x2717;
        </button>
      {/if}
      {#if s.outcome === 'failed' && s.agent_session_id}
        <button class="action-btn recover-btn" title="Attempt recovery" onclick={(e) => handleRecover(e, s.id)}>
          &#x21BB;
        </button>
      {/if}
    </span>
  </div>

  {#each active as child}
    {@render renderNode(child, depth + 1)}
  {/each}

  {#if terminal.length > 0}
    {#if isExpanded}
      {#each terminal as child}
        {@render renderNode(child, depth + 1)}
      {/each}
    {:else}
      <!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
      <div
        class="terminal-summary"
        style="padding-left: {0.5 + Math.min(depth + 1, 4) * 0.75}rem"
        onclick={(e) => { e.stopPropagation(); toggleExpand(s.id); }}
      >
        <span class="tree-branch">└</span>
        <span class="summary-text">{terminalSummaryText(terminal)}</span>
      </div>
    {/if}
  {/if}
{/snippet}

<div class="sidebar-tree">
  {#if leadSession}
    <div class="exec-meta">
      <span class="exec-meta-text">{execMetaBase()}</span>
      {#if worktreeExists && branchName !== undefined}
        <span class="exec-meta-sep">·</span>
        {#if branchName}
          <span class="exec-meta-branch">{branchName}</span>
          <CopyButton text={branchName} label="Copy branch name" />
        {:else}
          <span class="exec-meta-detached">detached</span>
        {/if}
      {/if}
    </div>
  {/if}
  {#if worktreePath}
    <div class="working-dir-row" title="Copy working directory path">
      <span class="working-dir-text">{worktreePath}</span>
      <CopyButton text={worktreePath} label="Copy working directory path" />
    </div>
  {/if}
  {#if (poolAgents ?? []).length > 0}
    <div class="pool-pills">
      {#each poolAgents ?? [] as entry (entry.agent_id)}
        <span class="pool-pill">{entry.name}</span>
      {/each}
    </div>
  {/if}
  <div class="tree-nodes">
    {#each tree as node}
      {@render renderNode(node, 0)}
    {/each}
  </div>
</div>

<style>
  .sidebar-tree {
    padding: 0.25rem 0;
    overflow-x: hidden;
  }

  .exec-meta {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.1875rem 0.75rem;
    font-size: 0.625rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    white-space: nowrap;
    min-width: 0;
  }

  .exec-meta-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .exec-meta-sep {
    flex-shrink: 0;
  }

  .exec-meta-branch {
    font-family: var(--font-mono, monospace);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .exec-meta-detached {
    font-family: var(--font-mono, monospace);
    font-style: italic;
    color: hsl(var(--muted-foreground) / 0.7);
  }

  .working-dir-row {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    padding: 0.1rem 0.75rem;
    min-width: 0;
    overflow: hidden;
  }

  .working-dir-text {
    flex: 1;
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono, monospace);
    font-weight: 500;
  }

  .pool-pills {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
    padding: 0.1875rem 0.75rem;
  }

  .pool-pill {
    display: inline-block;
    padding: 0.0625rem 0.3125rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--primary) / 0.1);
    color: hsl(var(--primary));
    font-size: 0.625rem;
    font-weight: 500;
  }

  .tree-nodes {
    display: flex;
    flex-direction: column;
  }

  .sidebar-node {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    width: 100%;
    box-sizing: border-box;
    text-align: left;
    padding-top: 0.1875rem;
    padding-bottom: 0.1875rem;
    padding-right: 0.5rem;
    border: none;
    background: transparent;
    cursor: pointer;
    font-size: 0.6875rem;
    color: hsl(var(--foreground));
    transition: background 0.1s;
    min-width: 0;
  }

  .sidebar-node:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .sidebar-node.active {
    background: hsl(var(--primary) / 0.1);
  }

  .depth-badge {
    font-size: 0.5rem;
    padding: 0 0.1875rem;
    border-radius: 2px;
    background: hsl(var(--muted-foreground) / 0.2);
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .tree-branch {
    color: hsl(var(--muted-foreground));
    font-size: 0.625rem;
    flex-shrink: 0;
  }

  .node-chevron {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 0.75rem;
    height: 0.75rem;
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

  .node-icon {
    width: 0.75rem;
    text-align: center;
    flex-shrink: 0;
    font-size: 0.625rem;
    font-weight: 700;
  }

  .working .node-icon { color: hsl(var(--status-working)); }
  .completed .node-icon { color: hsl(var(--status-success)); }
  .idle .node-icon { color: hsl(var(--status-attention)); }
  .crashed .node-icon { color: hsl(var(--status-danger)); }
  .failed .node-icon { color: hsl(var(--status-danger)); }
  .unassigned .node-icon, .stopped .node-icon, .canceled .node-icon { color: hsl(var(--muted-foreground)); }

  .node-label {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 0.25rem;
    overflow: hidden;
    min-width: 0;
    font-size: 0.6875rem;
    font-weight: 500;
  }

  .node-slug {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1 1 auto;
    min-width: 2rem;
  }

  .node-status {
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .context-bar {
    display: inline-flex;
    align-items: center;
    width: 2rem;
    height: 0.3125rem;
    background: hsl(var(--muted) / 0.4);
    border-radius: 2px;
    overflow: hidden;
    flex-shrink: 0;
  }

  .context-fill {
    height: 100%;
    border-radius: 2px;
    transition: width 0.3s ease;
  }

  .context-fill.ok { background: hsl(var(--status-success)); }
  .context-fill.warning { background: hsl(var(--status-attention)); }
  .context-fill.danger { background: hsl(var(--status-danger)); }

  .context-bar.unavailable {
    background: transparent;
    justify-content: center;
  }

  .context-dash {
    color: hsl(var(--muted-foreground));
    font-size: 0.5rem;
  }

  .context-bar.placeholder {
    visibility: hidden;
  }

  .action-zone {
    display: flex;
    align-items: center;
    gap: 0.125rem;
    flex-shrink: 0;
    width: 2.25rem;
    justify-content: flex-end;
  }

  .action-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1rem;
    height: 1rem;
    border: none;
    border-radius: var(--radius-sm);
    background: transparent;
    cursor: pointer;
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
    opacity: 0;
    transition: opacity 0.1s, color 0.1s, background 0.1s;
  }

  .sidebar-node:hover .action-zone .action-btn {
    opacity: 1;
  }

  .cancel-btn:hover {
    color: hsl(var(--status-danger));
    background: hsl(var(--status-danger) / 0.1);
  }

  .recover-btn:hover {
    color: hsl(var(--status-working));
    background: hsl(var(--status-working) / 0.1);
  }

  .terminal-summary {
    display: flex;
    align-items: center;
    gap: 0.25rem;
    box-sizing: border-box;
    padding-top: 0.1875rem;
    padding-bottom: 0.1875rem;
    padding-right: 0.5rem;
    cursor: pointer;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    font-style: italic;
    transition: background 0.1s;
  }

  .terminal-summary:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .summary-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
