<script lang="ts">
  import { createProjectMutation, updateProjectMutation, projectDetailQuery } from '../queries/projects';
  import { router } from '../router';
  import Button from './ui/button.svelte';

  interface Props {
    projectId?: string;
  }

  let { projectId }: Props = $props();

  const isEdit = !!projectId;
  const projectQuery = projectDetailQuery(() => projectId ?? null);
  const createMut = createProjectMutation();
  const updateMut = updateProjectMutation();

  let name = $state('');
  let path = $state('');
  let error: string | null = $state(null);
  let warning: string | null = $state(null);
  let created = $state(false);
  let createdId: string | null = $state(null);
  let initialized = false;

  // Populate fields from fetched project data (edit mode)
  $effect(() => {
    const project = projectQuery.data;
    if (project && !initialized) {
      initialized = true;
      name = project.name;
      path = project.path;
    }
  });

  let initialName = $derived(projectQuery.data?.name ?? '');
  let initialPath = $derived(projectQuery.data?.path ?? '');
  let isDirty = $derived(
    !created && (isEdit
      ? (name.trim() !== initialName || path.trim() !== initialPath)
      : (name.trim().length > 0 || path.trim().length > 0))
  );

  let submitting = $derived(createMut.isPending || updateMut.isPending);
  let canSubmit = $derived(name.trim().length > 0 && path.trim().length > 0 && !submitting && !created);

  // Navigation guard — warns on dirty form
  let guardCleanup: (() => void) | null = null;
  let unloadHandler: ((e: BeforeUnloadEvent) => void) | null = null;
  $effect(() => {
    if (isDirty) {
      guardCleanup = router.addNavigationGuard(() => true);
      unloadHandler = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
      window.addEventListener('beforeunload', unloadHandler);
    } else {
      guardCleanup?.();
      guardCleanup = null;
      if (unloadHandler) { window.removeEventListener('beforeunload', unloadHandler); unloadHandler = null; }
    }
    return () => {
      guardCleanup?.();
      guardCleanup = null;
      if (unloadHandler) { window.removeEventListener('beforeunload', unloadHandler); unloadHandler = null; }
    };
  });

  function clearGuard() {
    guardCleanup?.();
    guardCleanup = null;
    if (unloadHandler) { window.removeEventListener('beforeunload', unloadHandler); unloadHandler = null; }
  }

  async function handleSubmit() {
    if (!canSubmit) return;
    error = null;
    warning = null;

    try {
      if (isEdit && projectId) {
        const project = projectQuery.data!;
        const req: Record<string, unknown> = {};
        if (name.trim() !== project.name) req.name = name.trim();
        if (path.trim() !== project.path) req.path = path.trim();
        await updateMut.mutateAsync({ id: projectId, req });
        clearGuard();
        router.navigate(`/projects/${projectId}`);
      } else {
        const result = await createMut.mutateAsync({
          name: name.trim(),
          path: path.trim(),
        });
        if (result.warning) {
          warning = result.warning;
          created = true;
          createdId = result.id;
          return;
        }
        clearGuard();
        router.navigate(`/projects/${result.id}`);
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to save project';
    }
  }

  function handleCancel() {
    clearGuard();
    if (isEdit && projectId) {
      router.navigate(`/projects/${projectId}`);
    } else {
      router.navigate('/projects');
    }
  }

  function handleWarningClose() {
    clearGuard();
    if (createdId) {
      router.navigate(`/projects/${createdId}`);
    }
  }
</script>

<div class="form-panel scroll-thin">
  <div class="form-panel-header">
    <h2 class="form-panel-title">{isEdit ? 'Edit Project' : 'Register Project'}</h2>
    <div class="form-panel-actions">
      {#if created}
        <Button variant="default" onclick={handleWarningClose}>Close</Button>
      {:else}
        <Button variant="ghost" onclick={handleCancel}>Cancel</Button>
        <Button variant="default" disabled={!canSubmit} onclick={handleSubmit}>
          {submitting ? 'Saving...' : isEdit ? 'Save' : 'Register'}
        </Button>
      {/if}
    </div>
  </div>

  {#if isEdit && projectQuery.isLoading}
    <div class="form-loading">Loading project...</div>
  {:else if isEdit && projectQuery.isError}
    <div class="form-error-state">{projectQuery.error?.message ?? 'Failed to load project'}</div>
  {:else}
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
      <div class="form-error" role="alert">{error}</div>
    {/if}
    {#if warning}
      <div class="form-warning">{warning}</div>
    {/if}
  {/if}
</div>
