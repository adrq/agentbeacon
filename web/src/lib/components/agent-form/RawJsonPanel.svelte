<script lang="ts">
  interface Props {
    value: Record<string, unknown>;
    onchange: (value: Record<string, unknown>) => void;
    onerror: (hasError: boolean) => void;
  }

  let { value, onchange, onerror }: Props = $props();

  let open = $state(false);
  let text = $state(JSON.stringify(value, null, 2));
  let parseError: string | null = $state(null);
  let editing = $state(false);

  $effect(() => {
    const newText = JSON.stringify(value, null, 2);
    if (!editing && newText !== text) {
      text = newText;
      parseError = null;
      onerror(false);
    }
  });

  function handleInput(e: Event) {
    editing = true;
    text = (e.currentTarget as HTMLTextAreaElement).value;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        parseError = 'Must be a JSON object';
        onerror(true);
        return;
      }
      parseError = null;
      editing = false;
      onerror(false);
      onchange(parsed);
    } catch {
      parseError = 'Invalid JSON';
      onerror(true);
    }
  }
</script>

<div class="raw-json-panel">
  <button type="button" class="raw-json-toggle" onclick={() => open = !open}>
    <span class="form-section-label">Raw JSON</span>
    <span class="chevron" class:open={open}></span>
  </button>
  {#if open}
    <div class="raw-json-body">
      <textarea
        class="field-textarea mono raw-textarea"
        value={text}
        oninput={handleInput}
        rows="8"
      ></textarea>
      {#if parseError}
        <span class="field-error">{parseError}</span>
      {/if}
    </div>
  {/if}
</div>

<style>
  .raw-json-panel {
    margin-top: 0.5rem;
  }

  .raw-json-toggle {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    background: none;
    border: none;
    cursor: pointer;
    padding: 0;
    color: hsl(var(--muted-foreground));
  }

  .raw-json-toggle:hover {
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

  .raw-json-body {
    margin-top: 0.5rem;
    padding: 0.5rem;
    background: hsl(var(--card));
    border-radius: var(--radius-sm);
    border: 1px solid hsl(var(--border));
  }

  .raw-textarea {
    width: 100%;
    font-size: 0.8125rem;
  }
</style>
