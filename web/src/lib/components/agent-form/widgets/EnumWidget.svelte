<script lang="ts">
  import type { FieldAnnotation } from '../../../types';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    schema: Record<string, unknown>;
    onchange: (value: string | undefined) => void;
  }

  let { field, value, schema, onchange }: Props = $props();

  const enumValues: string[] = $derived.by(() => {
    const propName = field.pointer.split('/').pop() ?? '';
    const props = schema?.properties as Record<string, Record<string, unknown>> | undefined;
    const propSchema = props?.[propName];
    return (propSchema?.enum as string[]) ?? [];
  });
</script>

<div class="field">
  <label class="field-label" for={field.pointer}>{field.label}</label>
  <select
    id={field.pointer}
    class="field-select"
    value={typeof value === 'string' ? value : ''}
    onchange={(e) => {
      const v = e.currentTarget.value;
      onchange(v === '' ? undefined : v);
    }}
  >
    <option value="">-</option>
    {#each enumValues as opt}
      <option value={opt}>{opt}</option>
    {/each}
  </select>
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>
