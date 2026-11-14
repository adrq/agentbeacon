<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { untrack } from 'svelte';
  import {
    executionSecretsQuery,
    projectSecretsQuery,
    attachExecutionSecretMutation,
    detachExecutionSecretMutation,
    createAndAttachSecretMutation,
    secretErrorText,
  } from '../queries/secrets';
  import type { ExecutionSecret } from '../types';
  import Button from './ui/button.svelte';
  import ExecutionPoolDialog from './ExecutionPoolDialog.svelte';
  import SecretForm from './SecretForm.svelte';

  interface Props {
    executionId: string;
    projectId: string;
    live: boolean;
    open: boolean;
  }

  let { executionId, projectId, live, open = $bindable() }: Props = $props();

  const attachedQuery = executionSecretsQuery(() => executionId);
  const projectQuery = projectSecretsQuery(() => projectId);
  const attachMut = attachExecutionSecretMutation();
  const detachMut = detachExecutionSecretMutation();
  const createAndAttachMut = createAndAttachSecretMutation();

  let attachedKnown = $derived(attachedQuery.data !== undefined);
  let attachedNames = $derived(new Set((attachedQuery.data ?? []).map(s => s.name)));
  let projectKnown = $derived(projectQuery.data !== undefined);
  let candidates = $derived(
    attachedKnown && projectKnown
      ? projectQuery.data!.filter(s => !attachedNames.has(s.name)).map(s => ({ id: s.name, name: s.name }))
      : []
  );

  let createMessage = $state<string | null>(null);
  // Bumped on every open and every create; a request only reports to its own generation.
  let generation = 0;

  // Refetch both lists on open.
  $effect(() => {
    if (open) {
      generation++;
      createMessage = null;
      untrack(() => {
        attachedQuery.refetch();
        projectQuery.refetch();
      });
    }
  });

  async function attach(name: string) {
    await attachMut.mutateAsync({ executionId, projectId, name });
  }

  async function detach(name: string) {
    await detachMut.mutateAsync({ executionId, projectId, name });
  }

  async function createAndAttach(name: string, value: string): Promise<boolean> {
    const current = ++generation;
    createMessage = null;
    try {
      const { attachError } = await createAndAttachMut.mutateAsync({ executionId, projectId, name, value });
      if (current !== generation) return false;
      if (attachError) createMessage = `Created ${name} but could not attach it: ${secretErrorText(attachError)}`;
      return true;
    } catch (err) {
      if (current === generation) createMessage = `Failed to create secret: ${secretErrorText(err)}`;
      return false;
    }
  }
</script>

<ExecutionPoolDialog
  bind:open
  title="Execution secrets"
  description="Every session in this execution can fetch the secrets attached to it. Detaching a secret makes it unavailable from the next fetch."
  noun="secret"
  emptyText="No secrets attached to this execution."
  members={attachedQuery.data}
  loadError={attachedQuery.isError
    ? { message: attachedQuery.error?.message ?? 'Unknown error', retrying: attachedQuery.isFetching, onretry: () => attachedQuery.refetch() }
    : null}
  memberKey={(s: ExecutionSecret) => s.name}
  memberName={(s: ExecutionSecret) => s.name}
  onremove={detach}
  canAdd={live}
  {candidates}
  selectPlaceholder={!attachedKnown || !projectKnown ? 'Available once secrets load' : candidates.length === 0 ? 'No other project secrets' : 'Select a secret…'}
  addDisabled={!projectKnown}
  onadd={attach}
>
  {#snippet member(s: ExecutionSecret)}
    <span class="pool-member-text">
      <span class="pool-member-name" title={s.name}>{s.name}</span>
    </span>
  {/snippet}
  {#snippet addExtras()}
    {#if projectQuery.isError}
      <div class="pool-load-error">
        <p class="pool-error" role="alert">Failed to load project secrets: {secretErrorText(projectQuery.error)}</p>
        <Button variant="outline" size="sm" disabled={projectQuery.isFetching} onclick={() => projectQuery.refetch()}>
          {projectQuery.isFetching ? 'Retrying…' : 'Retry'}
        </Button>
      </div>
    {/if}
  {/snippet}
  {#snippet afterAdd()}
    <div class="secret-create">
      <span class="secret-create-label">Create a project secret and attach it</span>
      <SecretForm submitLabel="Create and attach" onsubmit={createAndAttach} />
      {#if createMessage}
        <p class="pool-error" role="alert">{createMessage}</p>
      {/if}
    </div>
  {/snippet}
</ExecutionPoolDialog>

<style>
  .secret-create {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
    padding-top: 0.5rem;
    border-top: 1px solid hsl(var(--border));
  }

  .secret-create-label {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }
</style>
