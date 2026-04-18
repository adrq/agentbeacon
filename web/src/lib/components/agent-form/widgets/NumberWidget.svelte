<script lang="ts">
  import type { FieldAnnotation } from '../../../types';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    onchange: (value: number | undefined) => void;
  }

  let { field, value, onchange }: Props = $props();
</script>

<div class="field">
  <label class="field-label" for={field.pointer}>{field.label}</label>
  <input
    id={field.pointer}
    class="field-input"
    type="number"
    style="font-variant-numeric: tabular-nums;"
    value={typeof value === 'number' ? value : ''}
    oninput={(e) => {
      const v = e.currentTarget.value;
      if (v === '') { onchange(undefined); return; }
      const n = Number(v);
      onchange(Number.isNaN(n) ? undefined : n);
    }}
  />
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>
