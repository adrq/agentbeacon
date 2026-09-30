<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import type { FieldAnnotation } from '../../../types';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    onchange: (value: boolean | undefined) => void;
  }

  let { field, value, onchange }: Props = $props();

  const checked = $derived(value === true);
</script>

<div class="field boolean-field">
  <label class="field-label">
    <input
      type="checkbox"
      checked={checked}
      onchange={(e) => {
        const v = e.currentTarget.checked;
        onchange(v ? true : undefined);
      }}
    />
    {field.label}
  </label>
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>

<style>
  .boolean-field input[type="checkbox"] {
    margin-right: 0.375rem;
  }
</style>
