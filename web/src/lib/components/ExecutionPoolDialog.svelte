<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts" generics="T">
  import { Dialog } from 'bits-ui';
  import type { Snippet } from 'svelte';
  import Button from './ui/button.svelte';

  interface Props {
    open: boolean;
    title: string;
    description: string;
    noun: string;
    emptyText: string;
    members: T[] | undefined;
    loadError: { message: string; retrying: boolean; onretry: () => void } | null;
    memberKey: (m: T) => string;
    memberName: (m: T) => string;
    member: Snippet<[T]>;
    onremove: (key: string) => Promise<void>;
    canAdd: boolean;
    candidates: { id: string; name: string }[];
    selectPlaceholder: string;
    addDisabled: boolean;
    onadd: (id: string) => Promise<void>;
    addExtras?: Snippet;
    afterAdd?: Snippet;
  }

  let {
    open = $bindable(),
    title,
    description,
    noun,
    emptyText,
    members,
    loadError,
    memberKey,
    memberName,
    member,
    onremove,
    canAdd,
    candidates,
    selectPlaceholder,
    addDisabled,
    onadd,
    addExtras,
    afterAdd,
  }: Props = $props();

  let known = $derived(members !== undefined);
  let plural = $derived(`${noun}s`);
  let capitalPlural = $derived(plural.charAt(0).toUpperCase() + plural.slice(1));

  let selectedId = $state('');
  let adding = $state(false);
  let removingKey = $state<string | null>(null);
  let actionError = $state<string | null>(null);

  // Reset the selection and error on open.
  $effect(() => {
    if (open) {
      selectedId = '';
      actionError = null;
    }
  });

  function errorText(err: unknown): string {
    return err instanceof Error ? err.message : 'Unknown error';
  }

  async function handleAdd() {
    if (!selectedId) return;
    actionError = null;
    adding = true;
    try {
      await onadd(selectedId);
      selectedId = '';
    } catch (err) {
      actionError = `Failed to add ${noun}: ${errorText(err)}`;
    } finally {
      adding = false;
    }
  }

  async function handleRemove(key: string) {
    actionError = null;
    removingKey = key;
    try {
      await onremove(key);
    } catch (err) {
      actionError = `Failed to remove ${noun}: ${errorText(err)}`;
    } finally {
      removingKey = null;
    }
  }
</script>

<Dialog.Root bind:open>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content pool-dialog">
      <Dialog.Title class="modal-title">{title}{known ? ` · ${members!.length}` : ''}</Dialog.Title>
      <Dialog.Description class="pool-help">{description}</Dialog.Description>

      {#if loadError}
        <div class="pool-load-error">
          <p class="pool-error" role="alert">{known ? `Failed to refresh ${plural}; the list below may be out of date` : `Failed to load ${plural}`}: {loadError.message}</p>
          <Button variant="outline" size="sm" disabled={loadError.retrying} onclick={() => loadError.onretry()}>
            {loadError.retrying ? 'Retrying…' : 'Retry'}
          </Button>
        </div>
      {/if}
      {#if !known}
        {#if !loadError}
          <p class="pool-empty">Loading {plural}&#8230;</p>
        {/if}
      {:else if members!.length === 0}
        <p class="pool-empty">{emptyText}</p>
      {:else}
        <ul class="pool-members scroll-thin" aria-label="{capitalPlural} in this execution">
          {#each members! as m (memberKey(m))}
            <li class="pool-member">
              {@render member(m)}
              <Button
                variant="ghost"
                size="sm"
                class="pool-remove"
                disabled={removingKey !== null}
                aria-label="Remove {memberName(m)} from execution"
                onclick={() => handleRemove(memberKey(m))}
              >{removingKey === memberKey(m) ? 'Removing…' : 'Remove'}</Button>
            </li>
          {/each}
        </ul>
      {/if}

      {#if canAdd}
        <form class="pool-add" onsubmit={(e) => { e.preventDefault(); handleAdd(); }}>
          <label class="pool-label" for="pool-add-select">Add {noun}</label>
          <div class="pool-add-row">
            <select
              id="pool-add-select"
              class="pool-select"
              bind:value={selectedId}
              disabled={candidates.length === 0 || adding}
            >
              <option value="">{selectPlaceholder}</option>
              {#each candidates as candidate (candidate.id)}
                <option value={candidate.id}>{candidate.name}</option>
              {/each}
            </select>
            <Button type="submit" variant="default" size="sm" class="pool-add-btn" disabled={!known || addDisabled || !selectedId || adding}>
              {adding ? 'Adding…' : 'Add'}
            </Button>
          </div>
          {@render addExtras?.()}
        </form>
        {@render afterAdd?.()}
      {/if}

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

  :global(.modal-content.pool-dialog .pool-error) {
    margin: 0;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--status-danger));
  }

  :global(.modal-content.pool-dialog .pool-load-error) {
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

  :global(.modal-content.pool-dialog .pool-member-text) {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  :global(.modal-content.pool-dialog .pool-member-name) {
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
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
  }
</style>
