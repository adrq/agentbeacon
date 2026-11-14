<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { AlertDialog, Dialog } from 'bits-ui';
  import {
    projectSecretsQuery,
    createProjectSecretMutation,
    rotateProjectSecretMutation,
    deleteProjectSecretMutation,
    secretErrorText,
  } from '../queries/secrets';
  import type { ProjectSecret } from '../types';
  import SecretForm from './SecretForm.svelte';

  interface Props {
    projectId: string;
  }

  let { projectId }: Props = $props();

  const secretsQuery = projectSecretsQuery(() => projectId);
  const createMut = createProjectSecretMutation();
  const rotateMut = rotateProjectSecretMutation();
  const deleteMut = deleteProjectSecretMutation();

  let secrets = $derived<ProjectSecret[]>(secretsQuery.data ?? []);

  let showAdd = $state(false);
  let addError = $state<string | null>(null);
  let replacing = $state<string | null>(null);
  let replaceError = $state<string | null>(null);
  let deleting = $state<string | null>(null);
  let deleteError = $state<string | null>(null);
  // Bumped on every dialog open and close; a request only updates its own dialog.
  let generation = 0;

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  }

  function openAdd() {
    generation++;
    addError = null;
    showAdd = true;
  }

  function openReplace(name: string) {
    generation++;
    replaceError = null;
    replacing = name;
  }

  function openDelete(name: string) {
    generation++;
    deleteError = null;
    deleting = name;
  }

  function closeAdd() {
    generation++;
    showAdd = false;
  }

  function closeReplace() {
    generation++;
    replacing = null;
  }

  function closeDelete() {
    generation++;
    deleting = null;
  }

  async function create(name: string, value: string): Promise<boolean> {
    const current = generation;
    addError = null;
    try {
      await createMut.mutateAsync({ projectId, name, value });
      if (current !== generation) return false;
      closeAdd();
      return true;
    } catch (err) {
      if (current === generation) addError = secretErrorText(err);
      return false;
    }
  }

  async function replace(name: string, value: string): Promise<boolean> {
    const current = generation;
    replaceError = null;
    try {
      await rotateMut.mutateAsync({ projectId, name, value });
      if (current !== generation) return false;
      closeReplace();
      return true;
    } catch (err) {
      if (current === generation) replaceError = secretErrorText(err);
      return false;
    }
  }

  async function handleDelete() {
    if (!deleting) return;
    const current = generation;
    deleteError = null;
    try {
      await deleteMut.mutateAsync({ projectId, name: deleting });
      if (current === generation) closeDelete();
    } catch (err) {
      if (current === generation) deleteError = secretErrorText(err);
    }
  }
</script>

{#if secretsQuery.isError}
  <p class="secrets-error" role="alert">Failed to load secrets: {secretErrorText(secretsQuery.error)}</p>
{:else if secrets.length === 0}
  <p class="secrets-empty">{secretsQuery.data ? 'No secrets in this project.' : 'Loading secrets…'}</p>
{:else}
  <ul class="secrets-list" aria-label="Project secrets">
    {#each secrets as secret (secret.name)}
      <li class="secret-row">
        <span class="secret-name" title={secret.name}>{secret.name}</span>
        <span class="secret-meta">
          Updated {formatDate(secret.updated_at)}
          {#if secret.attached_executions.length > 0}
            · attached to {secret.attached_executions.length} running execution{secret.attached_executions.length === 1 ? '' : 's'}:
            {#each secret.attached_executions as attached, i (attached.execution_id)}
              {#if i > 0}, {/if}<a class="secret-execution" href="#/execution/{attached.execution_id}" title={attached.execution_id}>{attached.execution_id.slice(0, 8)}</a>
            {/each}
          {/if}
        </span>
        <button class="secret-action" type="button" onclick={() => openReplace(secret.name)}>Replace value</button>
        <button class="secret-action secret-action-danger" type="button" aria-label="Delete {secret.name}" onclick={() => openDelete(secret.name)}>Delete</button>
      </li>
    {/each}
  </ul>
{/if}
<button class="secret-add-btn" type="button" onclick={openAdd}>+ Add Secret</button>

<Dialog.Root open={showAdd} onOpenChange={(open) => { if (!open) closeAdd(); }}>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content">
      <Dialog.Title class="modal-title">Add Secret</Dialog.Title>
      <Dialog.Description class="modal-description">
        Names use capitals, digits and underscores. The value is not shown again after you save it.
      </Dialog.Description>
      <SecretForm submitLabel="Create" onsubmit={create} />
      {#if addError}
        <div class="form-error secret-dialog-error" role="alert">{addError}</div>
      {/if}
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<Dialog.Root open={replacing !== null} onOpenChange={(open) => { if (!open) closeReplace(); }}>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content">
      <Dialog.Title class="modal-title">Replace Value</Dialog.Title>
      <Dialog.Description class="modal-description">
        Agents get the new value on their next fetch.
      </Dialog.Description>
      {#if replacing}
        <SecretForm name={replacing} submitLabel="Replace value" onsubmit={replace} />
      {/if}
      {#if replaceError}
        <div class="form-error secret-dialog-error" role="alert">{replaceError}</div>
      {/if}
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<AlertDialog.Root open={deleting !== null} onOpenChange={(open) => { if (!open) closeDelete(); }}>
  <AlertDialog.Portal>
    <AlertDialog.Overlay class="modal-overlay" />
    <AlertDialog.Content class="modal-content">
      <AlertDialog.Title class="modal-title">Delete Secret</AlertDialog.Title>
      <AlertDialog.Description class="modal-description">
        Delete "{deleting}"? Agents can no longer fetch it.
      </AlertDialog.Description>
      {#if deleteError}
        <div class="form-error" role="alert">{deleteError}</div>
      {/if}
      <div class="modal-actions">
        <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Cancel</AlertDialog.Cancel>
        <AlertDialog.Action class="alert-btn alert-btn-danger" onclick={handleDelete}>
          {deleteMut.isPending ? 'Deleting...' : 'Delete'}
        </AlertDialog.Action>
      </div>
    </AlertDialog.Content>
  </AlertDialog.Portal>
</AlertDialog.Root>

<style>
  .secrets-empty {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
    padding: 1rem 0;
  }

  .secrets-error {
    font-size: 0.8125rem;
    color: hsl(var(--status-danger));
  }

  .secrets-list {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
  }

  .secret-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    padding: 0.5rem 0.625rem;
    min-width: 0;
  }

  .secret-row + .secret-row {
    border-top: 1px solid hsl(var(--border));
  }

  .secret-name {
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .secret-meta {
    flex: 1;
    min-width: 0;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }

  .secret-execution {
    color: hsl(var(--primary));
  }

  .secret-action,
  .secret-add-btn {
    background: none;
    border: none;
    color: hsl(var(--primary));
    cursor: pointer;
    font-size: 0.75rem;
    padding: 0;
  }

  .secret-action-danger {
    color: hsl(var(--status-danger));
  }

  .secret-add-btn {
    margin-top: 0.5rem;
  }

  .secret-action:hover,
  .secret-add-btn:hover {
    opacity: 0.8;
  }

  .secret-dialog-error {
    margin: 0.75rem 0 0;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
