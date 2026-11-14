<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import { untrack } from 'svelte';
  import Button from './ui/button.svelte';

  interface Props {
    /** Fixed name, shown read-only (replacing a value). */
    name?: string;
    submitLabel: string;
    disabled?: boolean;
    /** Resolves true when the fields should be cleared. */
    onsubmit: (name: string, value: string) => Promise<boolean>;
  }

  let { name: fixedName, submitLabel, disabled = false, onsubmit }: Props = $props();

  const uid = $props.id();
  let name = $state(untrack(() => fixedName ?? ''));
  let value = $state('');
  let pending = $state(false);

  let ready = $derived(!disabled && !pending && name.length > 0 && value.length > 0);

  async function handleSubmit(e: SubmitEvent) {
    e.preventDefault();
    if (!ready) return;
    pending = true;
    try {
      if (await onsubmit(fixedName ?? name, value)) {
        value = '';
        if (fixedName === undefined) name = '';
      }
    } finally {
      pending = false;
    }
  }
</script>

<form class="secret-form" onsubmit={handleSubmit}>
  <label class="secret-label" for="{uid}-name">Name</label>
  <input
    id="{uid}-name"
    class="secret-input"
    type="text"
    placeholder="API_TOKEN"
    autocomplete="off"
    spellcheck="false"
    readonly={fixedName !== undefined}
    disabled={pending}
    bind:value={name}
  />
  <label class="secret-label" for="{uid}-value">Value</label>
  <textarea
    id="{uid}-value"
    class="secret-textarea"
    rows="4"
    autocomplete="off"
    spellcheck="false"
    disabled={pending}
    bind:value={value}
  ></textarea>
  <div class="secret-actions">
    <Button type="submit" variant="default" size="sm" disabled={!ready}>{submitLabel}</Button>
  </div>
</form>

<style>
  .secret-form {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
  }

  .secret-label {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .secret-input,
  .secret-textarea {
    width: 100%;
    padding: 0.375rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: var(--font-mono);
  }

  .secret-textarea {
    resize: vertical;
  }

  .secret-input:focus,
  .secret-textarea:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .secret-input[readonly] {
    color: hsl(var(--muted-foreground));
  }

  .secret-actions {
    display: flex;
    justify-content: flex-end;
  }
</style>
