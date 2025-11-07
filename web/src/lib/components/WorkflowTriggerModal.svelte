<script lang="ts">
  import { api } from '../api';
  import Button from './ui/button.svelte';

  export let open = false;

  let workflowYaml = '';
  let isSubmitting = false;
  let errorMessage = '';

  async function handleSubmit() {
    if (isSubmitting) return;

    errorMessage = '';
    isSubmitting = true;

    try {
      const executionId = await api.triggerWorkflowExecution(workflowYaml);

      // Success: close modal, clear state, navigate
      open = false;
      workflowYaml = '';
      window.location.hash = `#/run/${executionId}`;
    } catch (error) {
      // Error: display message, keep modal open, preserve textarea
      errorMessage = error instanceof Error ? error.message : 'Failed to submit workflow';
    } finally {
      isSubmitting = false;
    }
  }

  function handleCancel() {
    open = false;
    errorMessage = '';
  }

  function handleBackdropClick(event: MouseEvent) {
    if (event.target === event.currentTarget) {
      handleCancel();
    }
  }
</script>

{#if open}
  <div
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    onclick={handleBackdropClick}
    onkeydown={(e) => e.key === 'Escape' && handleCancel()}
    role="dialog"
    aria-modal="true"
    aria-labelledby="modal-title"
    tabindex="-1"
  >
    <div class="bg-background border border-border rounded-lg shadow-lg w-full max-w-3xl max-h-[90vh] flex flex-col">
      <!-- Header -->
      <div class="border-b border-border px-6 py-4">
        <h2 id="modal-title" class="text-lg font-semibold">New Workflow Execution</h2>
      </div>

      <!-- Content -->
      <div class="flex-1 overflow-y-auto px-6 py-4">
        <label for="workflow-yaml" class="block text-sm font-medium mb-2">
          Workflow YAML
        </label>
        <textarea
          id="workflow-yaml"
          data-testid="workflow-yaml-input"
          bind:value={workflowYaml}
          placeholder="Paste workflow YAML here..."
          class="w-full h-96 px-3 py-2 border border-border rounded-md bg-background font-mono text-sm focus:outline-none focus:ring-2 focus:ring-ring resize-none"
          disabled={isSubmitting}
        ></textarea>

        {#if errorMessage}
          <div
            class="mt-4 p-3 bg-destructive/10 border border-destructive/30 rounded-md text-destructive text-sm"
            data-testid="error-message"
          >
            {errorMessage}
          </div>
        {/if}
      </div>

      <!-- Footer -->
      <div class="border-t border-border px-6 py-4 flex justify-end gap-3">
        <Button
          variant="outline"
          onclick={handleCancel}
          disabled={isSubmitting}
        >
          Cancel
        </Button>
        <Button
          data-testid="submit-button"
          onclick={handleSubmit}
          disabled={isSubmitting || !workflowYaml.trim()}
        >
          {isSubmitting ? 'Submitting...' : 'Submit'}
        </Button>
      </div>
    </div>
  </div>
{/if}
