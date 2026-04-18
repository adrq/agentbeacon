<script lang="ts">
  import type { FieldAnnotation } from '../../../types';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    onchange: (value: unknown) => void;
    onerror?: (hasError: boolean) => void;
  }

  let { field, value, onchange, onerror }: Props = $props();

  let text = $state(value !== undefined ? JSON.stringify(value, null, 2) : '');
  let parseError: string | null = $state(null);

  $effect(() => {
    const newText = value !== undefined ? JSON.stringify(value, null, 2) : '';
    if (newText !== text && parseError === null) {
      text = newText;
    }
  });

  function handleInput(e: Event) {
    text = (e.currentTarget as HTMLTextAreaElement).value;
    if (!text.trim()) {
      parseError = null;
      onerror?.(false);
      onchange(undefined);
      return;
    }
    try {
      const parsed = JSON.parse(text);
      parseError = null;
      onerror?.(false);
      onchange(parsed);
    } catch {
      parseError = 'Invalid JSON';
      onerror?.(true);
    }
  }
</script>

<div class="field">
  <label class="field-label" for={field.pointer}>{field.label}</label>
  <textarea
    id={field.pointer}
    class="field-textarea mono"
    value={text}
    oninput={handleInput}
    rows="3"
  ></textarea>
  {#if parseError}
    <span class="field-error">{parseError}</span>
  {/if}
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>
