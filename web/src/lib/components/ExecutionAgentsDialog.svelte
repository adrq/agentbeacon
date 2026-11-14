<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { untrack } from 'svelte';
  import { executionAgentsQuery, addExecutionAgentMutation, removeExecutionAgentMutation } from '../queries/executions';
  import { agentsQuery } from '../queries/agents';
  import type { AgentPoolEntry } from '../types';
  import Button from './ui/button.svelte';
  import ExecutionPoolDialog from './ExecutionPoolDialog.svelte';

  interface Props {
    executionId: string;
    projectId: string | null;
    open: boolean;
  }

  let { executionId, projectId, open = $bindable() }: Props = $props();

  const poolQuery = executionAgentsQuery(() => executionId);
  const agentsQ = agentsQuery();
  const addMut = addExecutionAgentMutation();
  const removeMut = removeExecutionAgentMutation();

  // Membership is only known once the pool has loaded; until then nothing is offered for adding.
  let poolKnown = $derived(poolQuery.data !== undefined);
  let memberIds = $derived(new Set((poolQuery.data ?? []).map(m => m.agent_id)));
  let catalogKnown = $derived(agentsQ.data !== undefined);
  let candidates = $derived(
    poolKnown && catalogKnown
      ? agentsQ.data!.filter(a => a.enabled && !memberIds.has(a.id)).map(a => ({ id: a.id, name: a.name }))
      : []
  );

  let addToProject = $state(false);

  // Reset the opt-in and refetch the pool on open.
  $effect(() => {
    if (open) {
      addToProject = false;
      untrack(() => poolQuery.refetch());
    }
  });

  async function add(agentId: string) {
    await addMut.mutateAsync({ executionId, agentId, addToProject: !!projectId && addToProject, projectId });
    addToProject = false;
  }

  async function remove(agentId: string) {
    await removeMut.mutateAsync({ executionId, agentId });
  }
</script>

<ExecutionPoolDialog
  bind:open
  title="Execution agents"
  description="Agents in this pool can receive delegated work in this execution. Removing an agent prevents future delegation to it; it does not stop sessions that are already running."
  noun="agent"
  emptyText="No agents in this execution's pool."
  members={poolQuery.data}
  loadError={poolQuery.isError
    ? { message: poolQuery.error?.message ?? 'Unknown error', retrying: poolQuery.isFetching, onretry: () => poolQuery.refetch() }
    : null}
  memberKey={(m: AgentPoolEntry) => m.agent_id}
  memberName={(m: AgentPoolEntry) => m.name}
  onremove={remove}
  canAdd={true}
  {candidates}
  selectPlaceholder={!poolKnown || !catalogKnown ? 'Available once agents load' : candidates.length === 0 ? 'No other enabled agents' : 'Select an agent…'}
  addDisabled={!catalogKnown}
  onadd={add}
>
  {#snippet member(m: AgentPoolEntry)}
    <span class="pool-member-text">
      <span class="pool-member-name" title={m.name}>{m.name}</span>
      {#if m.description}
        <span class="pool-member-desc" title={m.description}>{m.description}</span>
      {/if}
    </span>
  {/snippet}
  {#snippet addExtras()}
    {#if agentsQ.isError}
      <div class="pool-load-error">
        <p class="pool-error" role="alert">Failed to load agent list: {agentsQ.error?.message ?? 'Unknown error'}</p>
        <Button variant="outline" size="sm" disabled={agentsQ.isFetching} onclick={() => agentsQ.refetch()}>
          {agentsQ.isFetching ? 'Retrying…' : 'Retry'}
        </Button>
      </div>
    {/if}
    {#if projectId}
      <label class="pool-checkbox">
        <input type="checkbox" bind:checked={addToProject} disabled={addMut.isPending} />
        Also add to project
      </label>
    {/if}
  {/snippet}
</ExecutionPoolDialog>

<style>
  .pool-member-desc {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .pool-checkbox {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--foreground));
    cursor: pointer;
  }

  .pool-checkbox input {
    accent-color: hsl(var(--primary));
  }

  @media (max-width: 768px) {
    .pool-checkbox {
      min-height: 2.5rem;
    }
  }
</style>
