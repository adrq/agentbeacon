<script lang="ts">
  import type { FieldAnnotation } from '../../../types';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    onchange: (value: Record<string, string>) => void;
  }

  let { field, value, onchange }: Props = $props();

  type Row = { key: string; val: string };

  function toRows(v: unknown): Row[] {
    if (typeof v === 'object' && v !== null && !Array.isArray(v)) {
      return Object.entries(v as Record<string, string>).map(([key, val]) => ({ key, val: String(val) }));
    }
    return [];
  }

  let rows: Row[] = $state(toRows(value));

  $effect(() => {
    if (typeof value === 'object' && value !== null) {
      const current = Object.fromEntries(rows.filter(r => r.key).map(r => [r.key, r.val]));
      if (JSON.stringify(current) !== JSON.stringify(value)) {
        rows = toRows(value);
      }
    }
  });

  function emit() {
    const result: Record<string, string> = {};
    for (const r of rows) {
      if (r.key.trim()) result[r.key.trim()] = r.val;
    }
    onchange(result);
  }

  function addRow() {
    rows = [...rows, { key: '', val: '' }];
  }

  function removeRow(idx: number) {
    rows = rows.filter((_, i) => i !== idx);
    emit();
  }
</script>

<div class="field">
  <label class="field-label">{field.label}</label>
  <div class="env-rows">
    {#each rows as row, idx}
      <div class="env-row">
        <input
          class="field-input mono env-key"
          type="text"
          placeholder="KEY"
          bind:value={row.key}
          oninput={emit}
        />
        <input
          class="field-input mono env-val"
          type="text"
          placeholder="value"
          bind:value={row.val}
          oninput={emit}
        />
        <button type="button" class="env-remove" onclick={() => removeRow(idx)}>&times;</button>
      </div>
    {/each}
  </div>
  <button type="button" class="env-add" onclick={addRow}>Add row</button>
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>

<style>
  .env-rows {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .env-row {
    display: flex;
    gap: 0.25rem;
    align-items: center;
  }

  .env-key {
    flex: 0 0 40%;
  }

  .env-val {
    flex: 1;
  }

  .env-remove {
    background: none;
    border: none;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    font-size: 1rem;
    padding: 0 0.25rem;
  }

  .env-remove:hover {
    color: hsl(var(--destructive));
  }

  .env-add {
    background: none;
    border: none;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    cursor: pointer;
    padding: 0.25rem 0;
    text-align: left;
  }

  .env-add:hover {
    color: hsl(var(--foreground));
  }
</style>
