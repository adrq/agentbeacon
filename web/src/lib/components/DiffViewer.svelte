<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import SplitPanel from './SplitPanel.svelte';
  import type { Theme } from '../types';

  export let filePath: string;
  export let beforeCode: string;
  export let afterCode: string;
  export let theme: Theme;

  const dispatch = createEventDispatcher<{
    acceptAll: void;
    rejectAll: void;
    reviewEach: void;
  }>();
</script>

<div class="diff-viewer" class:dark={theme === 'dark'}>
  <div class="diff-header">
    <span class="file-icon">📄</span>
    <h3 class="file-path">{filePath}</h3>
  </div>

  <SplitPanel storageKey="diff-viewer-split" initialLeftWidth={50}>
    <div slot="left" class="code-panel">
      <div class="panel-title">Before</div>
      <pre class="code-content"><code>{beforeCode}</code></pre>
    </div>
    <div slot="right" class="code-panel">
      <div class="panel-title">After</div>
      <pre class="code-content additions"><code>{afterCode}</code></pre>
    </div>
  </SplitPanel>

  <div class="diff-actions">
    <button class="btn-action accept" on:click={() => dispatch('acceptAll')}>
      ✓ Accept All
    </button>
    <button class="btn-action reject" on:click={() => dispatch('rejectAll')}>
      ✗ Reject All
    </button>
    <button class="btn-action review" on:click={() => dispatch('reviewEach')}>
      Review Each
    </button>
  </div>
</div>

<style>
  .diff-viewer {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 0.5rem;
  }

  .diff-viewer.dark {
    background: #1e293b;
    border-color: #334155;
  }

  .diff-header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 1rem;
    border-bottom: 1px solid #e2e8f0;
  }

  .diff-viewer.dark .diff-header {
    border-bottom-color: #334155;
  }

  .file-icon {
    font-size: 1.25rem;
  }

  .file-path {
    margin: 0;
    font-size: 0.9375rem;
    font-weight: 600;
    color: #0f172a;
    font-family: 'Monaco', 'Menlo', monospace;
  }

  .dark .file-path {
    color: #e2e8f0;
  }

  .code-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #f8fafc;
  }

  .dark .code-panel {
    background: #0f172a;
  }

  .panel-title {
    padding: 0.5rem 1rem;
    background: #f1f5f9;
    border-bottom: 1px solid #e2e8f0;
    font-size: 0.8125rem;
    font-weight: 600;
    color: #475569;
  }

  .dark .panel-title {
    background: #1e293b;
    border-bottom-color: #334155;
    color: #cbd5e1;
  }

  .code-content {
    flex: 1;
    margin: 0;
    padding: 1rem;
    overflow: auto;
    font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
    font-size: 0.8125rem;
    line-height: 1.6;
    color: #0f172a;
  }

  .dark .code-content {
    color: #e2e8f0;
  }

  .code-content.additions {
    background: #f0fdf4;
  }

  .dark .code-content.additions {
    background: #022c22;
  }

  .diff-actions {
    display: flex;
    gap: 0.75rem;
    padding: 1rem;
    border-top: 1px solid #e2e8f0;
  }

  .diff-viewer.dark .diff-actions {
    border-top-color: #334155;
  }

  .btn-action {
    padding: 0.5rem 1rem;
    border: 1px solid;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
  }

  .btn-action.accept {
    background: #10b981;
    color: #ffffff;
    border-color: #10b981;
  }

  .btn-action.accept:hover {
    background: #059669;
  }

  .btn-action.reject {
    background: #ef4444;
    color: #ffffff;
    border-color: #ef4444;
  }

  .btn-action.reject:hover {
    background: #dc2626;
  }

  .btn-action.review {
    background: #f1f5f9;
    color: #475569;
    border-color: #cbd5e1;
  }

  .btn-action.review:hover {
    background: #e2e8f0;
  }

  .dark .btn-action.review {
    background: #334155;
    color: #cbd5e1;
    border-color: #475569;
  }

  .dark .btn-action.review:hover {
    background: #475569;
  }
</style>
