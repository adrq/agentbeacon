<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import { EditorView, basicSetup } from 'codemirror';
  import { EditorState, StateEffect } from '@codemirror/state';
  import { yaml } from '@codemirror/lang-yaml';
  import { oneDark } from '@codemirror/theme-one-dark';

  export let value: string = '';
  export let theme: 'dark' | 'light' = 'dark';
  export let readOnly: boolean = false;

  const dispatch = createEventDispatcher<{
    change: string;
  }>();

  let editorContainer: HTMLDivElement;
  let editorView: EditorView | null = null;

  onMount(() => {
    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged && !readOnly) {
        const newValue = update.state.doc.toString();
        value = newValue;
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

    // Add read-only mode if enabled
    if (readOnly) {
      extensions.push(EditorState.readOnly.of(true));
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

  // Reactive statement to update theme dynamically
  $: if (editorView && theme) {
    const currentExtensions = [basicSetup, yaml()];

    if (theme === 'dark') {
      currentExtensions.push(oneDark);
    }

    if (readOnly) {
      currentExtensions.push(EditorState.readOnly.of(true));
    }

    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged && !readOnly) {
        const newValue = update.state.doc.toString();
        value = newValue;
        dispatch('change', value);
      }
    });
    currentExtensions.push(updateListener);

    editorView.dispatch({
      effects: StateEffect.reconfigure.of(currentExtensions)
    });
  }
</script>

<div class="workflow-editor">
  <div class="flex items-center justify-end mb-2">
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
    min-height: 200px;
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
