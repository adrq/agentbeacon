<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import { EditorView, basicSetup } from 'codemirror';
  import { yaml } from '@codemirror/lang-yaml';
  import { oneDark } from '@codemirror/theme-one-dark';

  export let value: string = '';
  export let theme: 'dark' | 'light' = 'dark';
  export let validating: boolean = false;

  const dispatch = createEventDispatcher<{
    change: string;
    validate: void;
    loadSample: void;
  }>();

  let editorContainer: HTMLDivElement;
  let editorView: EditorView | null = null;
  let hasUnsavedChanges = false;

  // Sample YAML workflow
  const sampleWorkflow = `name: "Sample Workflow"
description: "A simple example workflow"

tasks:
  - id: analyze
    agent: claude-code
    task:
      history:
        - kind: message
          messageId: "msg-1"
          role: user
          parts:
            - kind: text
              text: "Analyze the current codebase and identify potential improvements"

  - id: implement
    agent: gemini-cli
    task:
      history:
        - kind: message
          messageId: "msg-2"
          role: user
          parts:
            - kind: text
              text: "Implement the improvements suggested by the analysis"
    depends_on: [analyze]`;

  onMount(() => {
    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const newValue = update.state.doc.toString();
        value = newValue;
        hasUnsavedChanges = true;
        dispatch('change', value);
      }
    });

    const extensions = [
      basicSetup,
      yaml(),
      updateListener,
    ];

    // Add theme based on prop
    if (theme === 'dark') {
      extensions.push(oneDark);
    }

    editorView = new EditorView({
      doc: value,
      extensions,
      parent: editorContainer,
    });

    return () => {
      editorView?.destroy();
    };
  });

  function handleValidate() {
    dispatch('validate');
  }

  function handleLoadSample() {
    if (hasUnsavedChanges && value.trim().length > 0) {
      const confirmed = confirm('You have unsaved changes. Load sample workflow anyway?');
      if (!confirmed) return;
    }

    value = sampleWorkflow;
    hasUnsavedChanges = false;

    // Update CodeMirror editor
    if (editorView) {
      editorView.dispatch({
        changes: {
          from: 0,
          to: editorView.state.doc.length,
          insert: sampleWorkflow,
        },
      });
    }

    dispatch('loadSample');
  }
</script>

<div class="workflow-editor">
  <div class="flex items-center justify-between mb-2 gap-2">
    <div class="flex gap-2">
      <button
        class="inline-flex items-center rounded-md bg-primary text-primary-foreground text-[11px] font-medium px-2 py-1 hover:brightness-110 transition disabled:opacity-50 disabled:cursor-not-allowed"
        on:click={handleLoadSample}
        disabled={validating}
      >
        Load Sample
      </button>
      <button
        class="inline-flex items-center rounded-md bg-blue-600 text-white text-[11px] font-medium px-2 py-1 hover:brightness-110 transition disabled:opacity-50 disabled:cursor-not-allowed"
        on:click={handleValidate}
        disabled={validating}
      >
        {validating ? 'Validating...' : 'Validate Workflow'}
      </button>
    </div>
    <span class="editor-stats tabular-nums">{value.length} chars</span>
  </div>

  <div bind:this={editorContainer} class="editor-container code-surface flex-1"></div>
</div>

<style>
  .workflow-editor {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .editor-container {
    overflow: auto;
    border-radius: 4px;
  }

  .editor-container :global(.cm-editor) {
    height: 100%;
    font-size: 13px;
  }

  .editor-container :global(.cm-scroller) {
    overflow: auto;
  }

  /* Dark mode */
  @media (prefers-color-scheme: dark) {
    .code-surface {
      background: #0f172a;
    }
  }
</style>
