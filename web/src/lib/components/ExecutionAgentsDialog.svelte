<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { Dialog } from 'bits-ui';
  import { untrack } from 'svelte';
  import { executionAgentsQuery, addExecutionAgentMutation, removeExecutionAgentMutation } from '../queries/executions';
  import { agentsQuery } from '../queries/agents';
  import Button from './ui/button.svelte';

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

  let members = $derived(poolQuery.data ?? []);
  // Membership is only known once the pool has loaded; until then nothing is offered for adding.
  let poolKnown = $derived(poolQuery.data !== undefined);
  let memberIds = $derived(new Set(members.map(m => m.agent_id)));
  let catalogKnown = $derived(agentsQ.data !== undefined);
  let candidates = $derived(poolKnown && catalogKnown ? agentsQ.data!.filter(a => a.enabled && !memberIds.has(a.id)) : []);

  let selectedAgentId = $state('');
  let addToProject = $state(false);
  let removingId = $state<string | null>(null);
  let actionError = $state<string | null>(null);

  // Fresh form each time the dialog opens so a stale opt-in never carries over,
  // and a fresh pool read so changes made elsewhere show up.
  $effect(() => {
    if (open) {
      selectedAgentId = '';
      addToProject = false;
      actionError = null;
      untrack(() => poolQuery.refetch());
    }
  });

  function errorText(err: unknown): string {
    return err instanceof Error ? err.message : 'Unknown error';
  }

  async function handleAdd() {
    if (!selectedAgentId) return;
    actionError = null;
    try {
      await addMut.mutateAsync({ executionId, agentId: selectedAgentId, addToProject: !!projectId && addToProject, projectId });
      selectedAgentId = '';
      addToProject = false;
    } catch (err) {
      actionError = `Failed to add agent: ${errorText(err)}`;
    }
  }

  async function handleRemove(agentId: string) {
    actionError = null;
    removingId = agentId;
    try {
      await removeMut.mutateAsync({ executionId, agentId });
    } catch (err) {
      actionError = `Failed to remove agent: ${errorText(err)}`;
    } finally {
      removingId = null;
    }
  }
</script>

<Dialog.Root bind:open>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content pool-dialog">
      <Dialog.Title class="modal-title">Execution agents{poolKnown ? ` · ${members.length}` : ''}</Dialog.Title>
      <Dialog.Description class="pool-help">
        Agents in this pool can receive delegated work in this execution. Removing an agent prevents future delegation to it; it does not stop sessions that are already running.
      </Dialog.Description>

      {#if poolQuery.isError}
        <div class="pool-load-error">
          <p class="pool-error" role="alert">{poolKnown ? 'Failed to refresh agents; the list below may be out of date' : 'Failed to load agents'}: {poolQuery.error?.message ?? 'Unknown error'}</p>
          <Button variant="outline" size="sm" disabled={poolQuery.isFetching} onclick={() => poolQuery.refetch()}>
            {poolQuery.isFetching ? 'Retrying…' : 'Retry'}
          </Button>
        </div>
      {/if}
      {#if !poolKnown}
        {#if !poolQuery.isError}
          <p class="pool-empty">Loading agents&#8230;</p>
        {/if}
      {:else if members.length === 0}
        <p class="pool-empty">No agents in this execution's pool.</p>
      {:else}
        <ul class="pool-members scroll-thin" aria-label="Agents in this execution">
          {#each members as member (member.agent_id)}
            <li class="pool-member">
              <span class="pool-member-text">
                <span class="pool-member-name" title={member.name}>{member.name}</span>
                {#if member.description}
                  <span class="pool-member-desc" title={member.description}>{member.description}</span>
                {/if}
              </span>
              <Button
                variant="ghost"
                size="sm"
                class="pool-remove"
                disabled={removingId !== null}
                aria-label="Remove {member.name} from execution"
                onclick={() => handleRemove(member.agent_id)}
              >{removingId === member.agent_id ? 'Removing…' : 'Remove'}</Button>
            </li>
          {/each}
        </ul>
      {/if}

      <form class="pool-add" onsubmit={(e) => { e.preventDefault(); handleAdd(); }}>
        <label class="pool-label" for="pool-add-select">Add agent</label>
        <div class="pool-add-row">
          <select
            id="pool-add-select"
            class="pool-select"
            bind:value={selectedAgentId}
            disabled={candidates.length === 0 || addMut.isPending}
          >
            <option value="">{!poolKnown || !catalogKnown ? 'Available once agents load' : candidates.length === 0 ? 'No other enabled agents' : 'Select an agent…'}</option>
            {#each candidates as agent (agent.id)}
              <option value={agent.id}>{agent.name}</option>
            {/each}
          </select>
          <Button type="submit" variant="default" size="sm" class="pool-add-btn" disabled={!poolKnown || !catalogKnown || !selectedAgentId || addMut.isPending}>
            {addMut.isPending ? 'Adding…' : 'Add'}
          </Button>
        </div>
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
      </form>

      {#if actionError}
        <p class="pool-error" role="alert">{actionError}</p>
      {/if}

      <div class="pool-actions">
        <Dialog.Close class="alert-btn alert-btn-ghost">Done</Dialog.Close>
      </div>
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<style>
  :global(.modal-content.pool-dialog) {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    width: calc(100% - 2rem);
  }

  :global(.modal-content.pool-dialog .modal-title) {
    margin-bottom: 0;
  }

  :global(.pool-help) {
    margin: 0;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .pool-empty {
    margin: 0;
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .pool-error {
    margin: 0;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--status-danger));
  }

  .pool-load-error {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .pool-members {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 16rem;
    overflow-y: auto;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
  }

  .pool-member {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.375rem 0.5rem;
    min-width: 0;
  }

  .pool-member + .pool-member {
    border-top: 1px solid hsl(var(--border));
  }

  .pool-member-text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  .pool-member-name {
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .pool-member-desc {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .pool-add {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
  }

  .pool-label {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .pool-add-row {
    display: flex;
    gap: 0.5rem;
    min-width: 0;
  }

  .pool-select {
    flex: 1;
    min-width: 0;
    height: 1.75rem;
    padding: 0 0.375rem;
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
    background: hsl(var(--background));
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
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

  .pool-actions {
    display: flex;
    justify-content: flex-end;
  }

  @media (max-width: 768px) {
    .pool-add-row {
      flex-direction: column;
    }

    .pool-select {
      height: 2.5rem;
    }

    .pool-members {
      max-height: 40vh;
    }

    .pool-member :global(.pool-remove),
    .pool-add-row :global(.pool-add-btn) {
      min-height: 2.5rem;
    }

    .pool-checkbox {
      min-height: 2.5rem;
    }
  }
</style>
