<script lang="ts">
  import type { FieldAnnotation } from '../../../types';

  interface Props {
    field: FieldAnnotation;
    value: unknown;
    onchange: (value: string) => void;
  }

  let { field, value, onchange }: Props = $props();
  const isMono = ['command', 'api_key_env'].some(k => field.pointer.includes(k));
  const isIgnored = typeof field.support_status === 'object';
</script>

<div class="field">
  <label class="field-label" for={field.pointer}>{field.label}</label>
  <input
    id={field.pointer}
    class="field-input"
    class:mono={isMono}
    type={field.secret ? 'password' : 'text'}
    value={typeof value === 'string' ? value : ''}
    oninput={(e) => onchange(e.currentTarget.value)}
    readonly={isIgnored}
    placeholder={field.help_text ?? ''}
  />
  {#if field.help_text}
    <span class="field-hint">{field.help_text}</span>
  {/if}
</div>
