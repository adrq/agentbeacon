<script lang="ts">
  import { Dialog } from 'bits-ui';
  import type { Project } from '../types';
  import { createProjectMutation, updateProjectMutation } from '../queries/projects';
  import Button from './ui/button.svelte';

  interface Props {
    project?: Project;
    onsubmit?: () => void;
    oncancel?: () => void;
  }

  let { project, onsubmit, oncancel }: Props = $props();

  const createMut = createProjectMutation();
  const updateMut = updateProjectMutation();

  let isOpen = $state(true);
  let name = $state(project?.name ?? '');
  let path = $state(project?.path ?? '');
  let error: string | null = $state(null);
  let warning: string | null = $state(null);
  let created = $state(false);

  let isEdit = $derived(!!project);
  let submitting = $derived(createMut.isPending || updateMut.isPending);
  let canSubmit = $derived(name.trim().length > 0 && path.trim().length > 0 && !submitting && !created);

  async function handleSubmit() {
    if (!canSubmit) return;
    error = null;
    warning = null;

    try {
      if (isEdit && project) {
        const req: Record<string, unknown> = {};
        if (name.trim() !== project.name) req.name = name.trim();
        if (path.trim() !== project.path) req.path = path.trim();
        await updateMut.mutateAsync({ id: project.id, req });
      } else {
        const result = await createMut.mutateAsync({
          name: name.trim(),
          path: path.trim(),
        });
        if (result.warning) {
          warning = result.warning;
          created = true;
          return;
        }
      }
      onsubmit?.();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to save project';
    }
  }

  function handleClose() {
    isOpen = false;
    if (created) {
      onsubmit?.();
    } else {
      oncancel?.();
    }
  }
</script>

<Dialog.Root bind:open={isOpen} onOpenChange={(o) => { if (!o) handleClose(); }}>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content" aria-describedby={undefined}>
      <Dialog.Title class="modal-title">{isEdit ? 'Edit Project' : 'Register Project'}</Dialog.Title>

      <div class="field">
        <label class="field-label" for="project-name">Name</label>
        <input
          id="project-name"
          class="field-input"
          type="text"
          placeholder="my-app"
          bind:value={name}
        />
      </div>

      <div class="field">
        <label class="field-label" for="project-path">Path</label>
        <input
          id="project-path"
          class="field-input"
          type="text"
          placeholder="/home/user/code/my-app"
          bind:value={path}
        />
      </div>

      {#if error}
        <div class="modal-error" role="alert">{error}</div>
      {/if}
      {#if warning}
        <div class="modal-warning">{warning}</div>
      {/if}

      <div class="modal-actions">
        {#if created}
          <Button variant="default" onclick={handleClose}>Close</Button>
        {:else}
          <Button variant="ghost" onclick={handleClose}>Cancel</Button>
          <Button variant="default" disabled={!canSubmit} onclick={handleSubmit}>
            {submitting ? 'Saving...' : isEdit ? 'Save' : 'Register'}
          </Button>
        {/if}
      </div>
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<style>
  .field {
    margin-bottom: 1rem;
  }

  .field-label {
    display: block;
    font-size: 0.8125rem;
    font-weight: 500;
    margin-bottom: 0.375rem;
    color: hsl(var(--foreground));
  }

  .field-input {
    width: 100%;
    padding: 0.5rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: inherit;
  }

  .field-input:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .modal-error {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
    margin-bottom: 1rem;
  }

  .modal-warning {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-attention) / 0.1);
    color: hsl(var(--status-attention));
    font-size: 0.8125rem;
    margin-bottom: 1rem;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
