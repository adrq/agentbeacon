<script lang="ts">
  import type { FieldAnnotation } from '../../../types';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    onchange: (value: Record<string, unknown> | undefined) => void;
  }

  let { field, value, onchange }: Props = $props();

  const obj = $derived(typeof value === 'object' && value !== null ? value as Record<string, unknown> : {});
  const thinkingType = $derived(typeof obj.type === 'string' ? obj.type : '');
  const budgetTokens = $derived(typeof obj.budgetTokens === 'number' ? obj.budgetTokens : undefined);

  function update(type: string, tokens: number | undefined) {
    if (!type) {
      onchange(undefined);
      return;
    }
    const result: Record<string, unknown> = { type };
    if (tokens !== undefined) result.budgetTokens = tokens;
    onchange(result);
  }
</script>

<div class="field thinking-group">
  <label class="field-label">{field.label}</label>
  <div class="thinking-inline">
    <input
      class="field-input thinking-type"
      type="text"
      placeholder="type"
      value={thinkingType}
      oninput={(e) => update(e.currentTarget.value, budgetTokens)}
    />
    <input
      class="field-input thinking-tokens"
      type="number"
      placeholder="budgetTokens"
      style="font-variant-numeric: tabular-nums;"
      value={budgetTokens ?? ''}
      oninput={(e) => {
        const v = e.currentTarget.value;
        update(thinkingType, v === '' ? undefined : Number(v));
      }}
    />
  </div>
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>

<style>
  .thinking-inline {
    display: flex;
    gap: 0.5rem;
  }

  .thinking-type {
    flex: 1;
  }

  .thinking-tokens {
    flex: 0 0 8rem;
  }
</style>
