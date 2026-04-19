<script lang="ts">
  import type { FieldAnnotation, ModelSuggestion } from '../../../types';
  import { driverModelsQuery } from '../../../queries/agents';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    platform: string;
    onchange: (value: string) => void;
  }

  let { field, value, platform, onchange }: Props = $props();

  const modelsQuery = driverModelsQuery(() => platform);

  let showSuggestions = $state(false);
  let inputValue = $state(typeof value === 'string' ? value : '');

  $effect(() => {
    if (typeof value === 'string') inputValue = value;
  });

  const suggestions: ModelSuggestion[] = $derived(modelsQuery.data ?? []);

  function handleInput(e: Event) {
    const v = (e.currentTarget as HTMLInputElement).value;
    inputValue = v;
    onchange(v);
    showSuggestions = true;
  }

  function pickSuggestion(id: string) {
    inputValue = id;
    onchange(id);
    showSuggestions = false;
  }
</script>

<div class="field model-picker">
  <label class="field-label" for={field.pointer}>{field.label}</label>
  <input
    id={field.pointer}
    class="field-input mono"
    type="text"
    value={inputValue}
    oninput={handleInput}
    onfocus={() => showSuggestions = true}
    onblur={() => setTimeout(() => showSuggestions = false, 150)}
    placeholder="claude-opus-4-7"
    autocomplete="off"
  />
  {#if showSuggestions && suggestions.length > 0}
    <div class="model-suggestions">
      {#each suggestions as s}
        <button
          type="button"
          class="model-suggestion"
          class:recommended={s.recommended}
          onmousedown={(e) => { e.preventDefault(); pickSuggestion(s.id); }}
        >
          <span class="model-id mono">{s.id}</span>
          {#if s.description}
            <span class="model-desc">{s.description}</span>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>

<style>
  .model-picker {
    position: relative;
  }

  .model-suggestions {
    position: absolute;
    z-index: 10;
    top: calc(100% - 1.25rem);
    left: 0;
    right: 0;
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius-sm);
    max-height: 12rem;
    overflow-y: auto;
  }

  .model-suggestion {
    display: flex;
    flex-direction: column;
    gap: 0.125rem;
    width: 100%;
    padding: 0.375rem 0.5rem;
    border: none;
    background: transparent;
    text-align: left;
    cursor: pointer;
    font-size: 0.6875rem;
    color: hsl(var(--foreground));
  }

  .model-suggestion:hover {
    background: hsl(var(--accent));
  }

  .model-suggestion.recommended {
    border-left: 2px solid hsl(var(--primary));
  }

  .model-id {
    font-size: 0.6875rem;
    font-weight: 500;
  }

  .model-desc {
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
  }
</style>
