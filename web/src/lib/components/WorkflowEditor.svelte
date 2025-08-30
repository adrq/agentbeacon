<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  export let value: string = '';

  const dispatch = createEventDispatcher<{ change: string }>();

  let textareaElement: HTMLTextAreaElement;

  // Sample YAML workflow for placeholder
  const sampleWorkflow = `name: "Sample Workflow"
description: "A simple example workflow"

config:
  api_keys: "production"

on_error: "stop_all"

nodes:
  - id: analyze
    agent: claude-code
    prompt: |
      Analyze the current codebase and identify
      potential improvements
    timeout: 300

  - id: implement
    agent: gemini-cli
    prompt: |
      Implement the improvements suggested by:
      \${analyze.output}
    depends_on: [analyze]
    retry:
      attempts: 3
      backoff: exponential`;

  function handleInput(event: Event) {
    const target = event.target as HTMLTextAreaElement;
    value = target.value;
    dispatch('change', value);
  }

  function loadSample() {
    value = sampleWorkflow;
    dispatch('change', value);
  }
</script>

<div class="workflow-editor">
    <div class="flex items-center justify-between mb-2">
      <button class="inline-flex items-center rounded-md bg-primary text-primary-foreground text-[11px] font-medium px-2 py-1 hover:brightness-110 transition" on:click={loadSample}>
        Load Sample
      </button>
      <span class="editor-stats tabular-nums">{value.length} chars</span>
    </div>

  <textarea
    bind:this={textareaElement}
    bind:value={value}
    on:input={handleInput}
    placeholder="Enter your workflow YAML here..."
    class="code-surface w-full flex-1 min-h-[420px] p-4 resize-none scroll-thin"
    spellcheck="false"
    autocomplete="off"
  ></textarea>
</div>

<style>
  .workflow-editor { height: 100%; display: flex; flex-direction: column; }

  /* Legacy CSS largely removed; using utility classes + tokens */

  /* Dark mode */
  @media (prefers-color-scheme: dark) {
    .code-surface { background: #0f172a; }
  }
</style>
