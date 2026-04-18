<script lang="ts">
  import type { DriverDescriptor, FieldAnnotation } from '../../types';
  import TextWidget from './widgets/TextWidget.svelte';
  import NumberWidget from './widgets/NumberWidget.svelte';
  import EnumWidget from './widgets/EnumWidget.svelte';
  import ModelPickerWidget from './widgets/ModelPickerWidget.svelte';
  import EnvMapWidget from './widgets/EnvMapWidget.svelte';
  import ProviderWidget from './widgets/ProviderWidget.svelte';
  import ThinkingWidget from './widgets/ThinkingWidget.svelte';
  import BooleanWidget from './widgets/BooleanWidget.svelte';
  import RawJsonWidget from './widgets/RawJsonWidget.svelte';

  interface Props {
    descriptor: DriverDescriptor;
    value: Record<string, unknown>;
    onchange: () => void;
    onerror?: (hasError: boolean) => void;
  }

  let { descriptor, value, onchange, onerror }: Props = $props();

  const basicFields = $derived(descriptor.fields.filter(f => f.group === 'basic'));
  const advancedFields = $derived(descriptor.fields.filter(f => f.group === 'advanced'));

  let advancedOpen = $state(false);

  // Track per-field errors from JSON widgets
  let fieldErrors: Record<string, boolean> = $state({});

  function fieldKey(field: FieldAnnotation): string {
    return field.pointer.split('/').pop() ?? '';
  }

  function setField(field: FieldAnnotation, val: unknown) {
    const key = fieldKey(field);
    if (val === undefined || val === '' || val === null) {
      delete value[key];
    } else {
      value[key] = val;
    }
    onchange();
  }

  function setFieldError(field: FieldAnnotation, hasError: boolean) {
    const key = fieldKey(field);
    if (hasError) {
      fieldErrors[key] = true;
    } else {
      delete fieldErrors[key];
    }
    fieldErrors = { ...fieldErrors };
    onerror?.(Object.keys(fieldErrors).length > 0);
  }
</script>

{#snippet renderField(field: FieldAnnotation)}
  {@const key = fieldKey(field)}
  {#if field.widget === 'model_picker'}
    <ModelPickerWidget
      {field}
      value={value[key]}
      platform={descriptor.platform}
      onchange={(v) => setField(field, v)}
    />
  {:else if field.widget === 'enum'}
    <EnumWidget
      {field}
      value={value[key]}
      schema={descriptor.schema as Record<string, unknown>}
      onchange={(v) => setField(field, v)}
    />
  {:else if field.widget === 'number'}
    <NumberWidget
      {field}
      value={value[key]}
      onchange={(v) => setField(field, v)}
    />
  {:else if field.widget === 'env_map'}
    <EnvMapWidget
      {field}
      value={value[key]}
      onchange={(v) => setField(field, v)}
    />
  {:else if field.widget === 'provider'}
    <ProviderWidget
      {field}
      value={value[key]}
      onchange={(v) => setField(field, v)}
      onerror={(hasErr) => setFieldError(field, hasErr)}
    />
  {:else if field.widget === 'thinking'}
    <ThinkingWidget
      {field}
      value={value[key]}
      onchange={(v) => setField(field, v)}
    />
  {:else if field.widget === 'raw_json'}
    <RawJsonWidget
      {field}
      value={value[key]}
      onchange={(v) => setField(field, v)}
      onerror={(hasErr) => setFieldError(field, hasErr)}
    />
  {:else if field.widget === 'boolean'}
    <BooleanWidget
      {field}
      value={value[key]}
      onchange={(v) => setField(field, v)}
    />
  {:else}
    <TextWidget
      {field}
      value={value[key]}
      onchange={(v) => setField(field, v)}
    />
  {/if}
{/snippet}

{#each basicFields as field (field.pointer)}
  {@render renderField(field)}
{/each}

{#if advancedFields.length > 0}
  <hr class="form-section-divider" />
  <button type="button" class="form-section-toggle" onclick={() => advancedOpen = !advancedOpen}>
    <span class="form-section-label">Advanced</span>
    <span class="chevron" class:open={advancedOpen}></span>
  </button>
  {#if advancedOpen}
    {#each advancedFields as field (field.pointer)}
      {@render renderField(field)}
    {/each}
  {/if}
{/if}

<style>
  .form-section-toggle {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    background: none;
    border: none;
    cursor: pointer;
    padding: 0;
    color: hsl(var(--muted-foreground));
  }

  .form-section-toggle:hover {
    color: hsl(var(--foreground));
  }

  .chevron {
    display: inline-block;
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 4px solid currentColor;
    transition: transform 0.15s;
  }

  .chevron.open {
    transform: rotate(180deg);
  }
</style>
