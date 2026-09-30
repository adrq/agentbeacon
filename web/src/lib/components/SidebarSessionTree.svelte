<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import type { SessionSummary, Agent, UsageState, WorktreeInfo, SessionIdentity } from '../types';
  import { buildTree, partitionChildren, terminalSummaryText, containsSession, type TreeNode } from '../utils/treeLayout';
  import { formatTokens } from '../format';
  import { sessionStatusLabel } from '../sessionStatus';
  import { api } from '../api';
  import { toasts } from '../stores/toasts';
  import CopyButton from './CopyButton.svelte';
  import { agentPoolDialogExecutionId } from '../stores/appState';

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

  // Short label for the fixed-width status slot; the full label goes in the title.
  function compactStatusLabel(status: string, desiredBy: string | null | undefined): string {
    if (status === 'stopped' && desiredBy === 'system:restart') return 'paused';
    return status;
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
  {@const usagePct = usage?.available && usage?.supportsContextPercentage && (usage?.contextWindow ?? 0) > 0
    ? Math.max(0, Math.min(100, Math.round(100 * (usage?.usedTokens ?? 0) / (usage?.contextWindow ?? 1))))
    : null}
  {@const usageLevel = usagePct !== null ? (usagePct >= 90 ? 'danger' : usagePct >= 70 ? 'warning' : 'ok') : null}
  {@const usageTitle = usagePct !== null && usage
    ? `${formatTokens(usage.usedTokens)} / ${formatTokens(usage.contextWindow)} (${usagePct}%)`
    : usage && !usage.available ? 'Context tracking unavailable' : ''}
  {@const identity = sessionIdentity?.get(s.id)}
  {@const fullStatus = sessionStatusLabel(s.status, s.desired_by)}
  {@const ringColor = usageLevel === 'danger' ? 'var(--status-danger)' : usageLevel === 'warning' ? 'var(--status-attention)' : 'var(--status-success)'}

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
      <span class="node-slug" title={identity?.hierarchicalName ?? undefined}>{identity?.slug ?? agentName(s.agent_id)}</span>
      {#if identity?.agentName}
        <span class="node-agent" title="Agent: {identity.agentName}{identity.role ? ` (${identity.role})` : ''}">{identity.agentName}</span>
      {/if}
    </span>
    <span class="node-status" title={fullStatus}>{compactStatusLabel(s.status, s.desired_by)}</span>
    {#if usagePct !== null}
      <span
        class="context-ring"
        role="meter"
        aria-label="Context: {usagePct}%"
        aria-valuenow={usagePct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <svg viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">
          <circle class="ring-track" cx="7" cy="7" r="6" />
          <circle
            class="ring-arc"
            cx="7" cy="7" r="6"
            pathLength="100"
            stroke="hsl({ringColor})"
            stroke-dasharray="{usagePct} 100"
            transform="rotate(-90 7 7)"
          />
        </svg>
      </span>
    {:else if usage && !usage.available}
      <span class="context-ring unavailable"><span class="context-dash">&mdash;</span></span>
    {:else}
      <span class="context-ring placeholder" aria-hidden="true"></span>
    {/if}
    <span class="action-zone">
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
  {#if leadSession}
    <!-- poolAgents is undefined while the pool is loading or unavailable -->
    <div class="pool-row">
      <button
        class="pool-action"
        aria-label={poolAgents ? `Manage execution agents (${poolAgents.length})` : 'Manage execution agents'}
        title={poolAgents ? poolAgents.map(a => a.name).join(', ') || 'No agents' : undefined}
        onclick={() => agentPoolDialogExecutionId.set(leadSession!.execution_id)}
      >{poolAgents ? `Agents · ${poolAgents.length}` : 'Agents'}</button>
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

  .pool-row {
    display: flex;
    padding: 0.125rem 0.75rem;
  }

  .pool-action {
    padding: 0.0625rem 0.3125rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.625rem;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
    transition: background 0.1s, color 0.1s;
  }

  .pool-action:hover,
  .pool-action:focus-visible {
    background: hsl(var(--muted) / 0.5);
    color: hsl(var(--foreground));
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

  /* The slug keeps its natural width (normal slugs are at most 11 chars);
     the agent name absorbs almost all shrinkage before the slug truncates. */
  .node-slug {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 0 1 auto;
    min-width: 2rem;
  }

  .node-agent {
    flex: 0 1000 auto;
    min-width: 0;
    max-width: 7rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
  }

  .node-status {
    width: 3.75rem;
    flex-shrink: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: right;
    font-size: 0.625rem;
    color: hsl(var(--muted-foreground));
  }

  .context-ring {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    flex-shrink: 0;
  }

  .context-ring svg {
    display: block;
  }

  .ring-track,
  .ring-arc {
    fill: none;
    stroke-width: 2;
  }

  .ring-track {
    stroke: hsl(var(--muted));
  }

  .ring-arc {
    transition: stroke-dasharray 0.3s ease;
  }

  .context-dash {
    color: hsl(var(--muted-foreground));
    font-size: 0.5rem;
  }

  .context-ring.placeholder {
    visibility: hidden;
  }

  .action-zone {
    display: flex;
    align-items: center;
    flex-shrink: 0;
    width: 1rem;
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

  .sidebar-node:hover .action-zone .action-btn,
  .action-btn:focus-visible {
    opacity: 1;
  }

  @media (hover: none) {
    .action-btn {
      opacity: 1;
    }
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
