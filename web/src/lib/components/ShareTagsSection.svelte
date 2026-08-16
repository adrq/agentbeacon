<script lang="ts">
  import { AlertDialog } from 'bits-ui';
  import { ApiError } from '../api';
  import { projectsQuery } from '../queries/projects';
  import {
    shareTagsQuery,
    addShareTagMemberMutation,
    updateShareTagMemberMutation,
    removeShareTagMemberMutation,
  } from '../queries/wiki';
  import type { MembershipConfirmation, ShareTag, ShareTagMember, WikiAccess } from '../types';

  const tags = shareTagsQuery();
  const projects = projectsQuery();
  const addMut = addShareTagMemberMutation();
  const updateMut = updateShareTagMemberMutation();
  const removeMut = removeShareTagMemberMutation();

  // The add form replaces the list: one membership change is edited at a time.
  let addingTagId = $state<string | null>(null);
  let formProjectId = $state('');
  let formAccess = $state<WikiAccess>('read');
  let formError = $state<string | null>(null);
  let listError = $state<string | null>(null);

  // Access levels chosen in a row but not yet accepted by the server.
  let pendingAccess = $state<Record<string, WikiAccess>>({});

  interface PendingChange {
    kind: 'admit' | 'upgrade';
    tagId: string;
    tagName: string;
    project: string;
    projectName: string;
    projectSlug: string;
    accessLevel: WikiAccess;
    confirmation: MembershipConfirmation;
  }

  let pending = $state<PendingChange | null>(null);
  let confirmError = $state<string | null>(null);
  let showExposedPages = $state(false);

  let tagList = $derived(tags.data ?? []);
  let addingTag = $derived<ShareTag | null>(
    addingTagId ? (tagList.find(t => t.tag_id === addingTagId) ?? null) : null
  );

  function projectName(id: string, fallback: string): string {
    return (projects.data ?? []).find(p => p.id === id)?.name ?? fallback;
  }

  function projectSlug(id: string, fallback: string): string {
    return (projects.data ?? []).find(p => p.id === id)?.slug ?? fallback;
  }

  function rowKey(tagId: string, projectId: string): string {
    return `${tagId}:${projectId}`;
  }

  function accessValue(tagId: string, member: ShareTagMember): WikiAccess {
    return pendingAccess[rowKey(tagId, member.project_id)] ?? member.access_level;
  }

  function availableProjects(tag: ShareTag) {
    const members = new Set(tag.members.map(m => m.project_id));
    return (projects.data ?? []).filter(p => !members.has(p.id));
  }

  function pageWord(count: number): string {
    return count === 1 ? '1 page' : `${count} pages`;
  }

  function verb(count: number): string {
    return count === 1 ? 'becomes' : 'become';
  }

  function confirmationOf(e: unknown): MembershipConfirmation | null {
    if (!(e instanceof ApiError) || e.status !== 409) return null;
    try {
      const body = JSON.parse(e.body);
      return body.error === 'membership_requires_confirmation' ? body : null;
    } catch {
      return null;
    }
  }

  function errorMessage(e: unknown): string {
    if (e instanceof ApiError) {
      try {
        const body = JSON.parse(e.body);
        if (typeof body.remedy === 'string') return body.remedy;
        if (typeof body.error === 'string') return body.error;
      } catch { /* not a JSON body */ }
    }
    return e instanceof Error ? e.message : 'Request failed';
  }

  function openAddForm(tag: ShareTag) {
    addingTagId = tag.tag_id;
    formProjectId = '';
    formAccess = 'read';
    formError = null;
    listError = null;
  }

  function closeAddForm() {
    addingTagId = null;
    formProjectId = '';
    formError = null;
  }

  async function submitAdd() {
    if (!addingTag) return;
    if (!formProjectId) { formError = 'Choose a project to admit.'; return; }
    formError = null;
    const tag = addingTag;
    try {
      await addMut.mutateAsync({
        tagId: tag.tag_id,
        project: formProjectId,
        accessLevel: formAccess,
      });
      await tags.refetch();
      closeAddForm();
    } catch (e) {
      const confirmation = confirmationOf(e);
      if (!confirmation) { formError = errorMessage(e); return; }
      pending = {
        kind: 'admit',
        tagId: tag.tag_id,
        tagName: tag.tag,
        project: formProjectId,
        projectName: projectName(formProjectId, formProjectId),
        projectSlug: projectSlug(formProjectId, formProjectId),
        accessLevel: formAccess,
        confirmation,
      };
    }
  }

  async function changeAccess(tag: ShareTag, member: ShareTagMember, next: WikiAccess) {
    listError = null;
    const key = rowKey(tag.tag_id, member.project_id);
    pendingAccess = { ...pendingAccess, [key]: next };
    try {
      await updateMut.mutateAsync({
        tagId: tag.tag_id,
        project: member.project_id,
        accessLevel: next,
      });
      await tags.refetch();
      clearPendingAccess(key);
    } catch (e) {
      const confirmation = confirmationOf(e);
      if (!confirmation) {
        clearPendingAccess(key);
        listError = errorMessage(e);
        return;
      }
      pending = {
        kind: 'upgrade',
        tagId: tag.tag_id,
        tagName: tag.tag,
        project: member.project_id,
        projectName: projectName(member.project_id, member.project_slug),
        projectSlug: member.project_slug,
        accessLevel: next,
        confirmation,
      };
    }
  }

  function clearPendingAccess(key: string) {
    const next = { ...pendingAccess };
    delete next[key];
    pendingAccess = next;
  }

  async function revoke(tag: ShareTag, member: ShareTagMember) {
    listError = null;
    try {
      await removeMut.mutateAsync({ tagId: tag.tag_id, project: member.project_id });
      await tags.refetch();
    } catch (e) {
      listError = errorMessage(e);
    }
  }

  async function confirmPending() {
    if (!pending) return;
    const change = pending;
    confirmError = null;
    try {
      if (change.kind === 'admit') {
        await addMut.mutateAsync({
          tagId: change.tagId,
          project: change.project,
          accessLevel: change.accessLevel,
          acknowledgeShare: true,
        });
      } else {
        await updateMut.mutateAsync({
          tagId: change.tagId,
          project: change.project,
          accessLevel: change.accessLevel,
          acknowledgeShare: true,
        });
      }
      await tags.refetch();
      clearPendingAccess(rowKey(change.tagId, change.project));
      pending = null;
      showExposedPages = false;
      closeAddForm();
    } catch (e) {
      // The dialog closes on the action click, so the failure has to survive it.
      confirmError = errorMessage(e);
      listError = confirmError;
    }
  }

  function cancelPending() {
    if (pending) clearPendingAccess(rowKey(pending.tagId, pending.project));
    pending = null;
    confirmError = null;
    showExposedPages = false;
  }

  let exposesTo = $derived(pending?.confirmation.exposes_to_joiner ?? []);
  let exposesToCount = $derived(exposesTo.reduce((sum, g) => sum + g.page_count, 0));
  let exposesFrom = $derived(pending?.confirmation.exposes_from_joiner ?? null);
  let grantsWrite = $derived(pending?.confirmation.grants_write ?? []);
  let grantsWriteCount = $derived(grantsWrite.reduce((sum, g) => sum + g.page_count, 0));
  // One row per page, carrying every way this change touches it. Keyed by
  // owning project as well as slug, since two members may hold the same slug.
  let exposedPages = $derived.by(() => {
    const byPage = new Map<string, { slug: string; kinds: string[] }>();
    const add = (groups: { project: string; pages: { slug: string }[] }[], kind: string) => {
      for (const group of groups) {
        for (const page of group.pages) {
          const key = `${group.project}/${page.slug}`;
          const entry = byPage.get(key) ?? { slug: page.slug, kinds: [] };
          if (!entry.kinds.includes(kind)) entry.kinds.push(kind);
          byPage.set(key, entry);
        }
      }
    };
    add(exposesTo, 'visible to');
    if (exposesFrom) add([{ project: pending?.projectSlug ?? '', pages: exposesFrom.pages }], 'visible from');
    add(grantsWrite, 'editable by');
    return [...byPage.values()];
  });
</script>

<div class="share-tags">
  {#if tags.isLoading}
    <p class="empty-text">Loading share tags...</p>
  {:else if tags.isError}
    <p class="empty-text">Failed to load share tags: {tags.error?.message ?? 'Unknown error'}</p>
  {:else if tagList.length === 0}
    <p class="empty-text">No wiki tags yet.</p>
  {:else if addingTag}
    <div class="add-form">
      <div class="add-form-title">Admit a project to {addingTag.tag}</div>
      <label class="field">
        Member project
        <select
          class="field-input"
          aria-label="Member project"
          value={formProjectId}
          onchange={(e) => formProjectId = e.currentTarget.value}
        >
          <option value="">Select a project...</option>
          {#each availableProjects(addingTag) as project (project.id)}
            <option value={project.id}>{project.name}</option>
          {/each}
        </select>
      </label>
      <label class="field">
        Access level
        <select
          class="field-input"
          aria-label="Access level"
          value={formAccess}
          onchange={(e) => formAccess = e.currentTarget.value as WikiAccess}
        >
          <option value="read">read</option>
          <option value="read_write">read_write</option>
        </select>
      </label>
      {#if formError}
        <div class="form-error" role="alert">{formError}</div>
      {/if}
      <div class="add-actions">
        <button class="ghost-btn" onclick={closeAddForm}>Cancel</button>
        <button class="primary-btn" disabled={addMut.isPending} onclick={submitAdd}>Add member</button>
      </div>
    </div>
  {:else}
    {#if listError}
      <div class="form-error" role="alert">{listError}</div>
    {/if}
    <div class="tag-list">
      {#each tagList as tag (tag.tag_id)}
        <div class="tag-row">
          <div class="tag-head">
            <span class="tag-name">{tag.tag}</span>
            <button
              class="ghost-btn"
              aria-label="Add member to {tag.tag}"
              onclick={() => openAddForm(tag)}
            >Add member</button>
          </div>
          {#if tag.members.length === 0}
            <p class="empty-text">No member projects.</p>
          {:else}
            <ul class="member-list">
              {#each tag.members as member (member.project_id)}
                <li class="member-row">
                  <span class="member-name">{projectName(member.project_id, member.project_slug)}</span>
                  <select
                    class="access-select"
                    aria-label="Access level for {projectName(member.project_id, member.project_slug)}"
                    value={accessValue(tag.tag_id, member)}
                    onchange={(e) => changeAccess(tag, member, e.currentTarget.value as WikiAccess)}
                  >
                    <option value="read">read</option>
                    <option value="read_write">read_write</option>
                  </select>
                  <button class="ghost-btn" onclick={() => revoke(tag, member)}>Revoke</button>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<AlertDialog.Root open={pending !== null} onOpenChange={(open) => { if (!open) cancelPending(); }}>
  <AlertDialog.Portal>
    <AlertDialog.Overlay class="modal-overlay" />
    <AlertDialog.Content class="modal-content">
      {#if pending}
        <AlertDialog.Title class="modal-title">
          {#if pending.kind === 'upgrade'}
            Grant write access on {pending.tagName} to {pending.projectName}?
          {:else}
            Share {pending.tagName} with {pending.projectName}?
          {/if}
        </AlertDialog.Title>
        <AlertDialog.Description class="modal-description">
          {#if pending.kind === 'upgrade'}
            <span class="exposure-line">
              {pageWord(grantsWriteCount)} {verb(grantsWriteCount)} editable by {pending.projectSlug}.
            </span>
          {:else}
            <span class="exposure-line">
              {pageWord(exposesToCount)} {verb(exposesToCount)} visible to {pending.projectSlug}.
            </span>
            <span class="exposure-line">
              {pageWord(exposesFrom?.page_count ?? 0)} {verb(exposesFrom?.page_count ?? 0)} visible from {pending.projectSlug}.
            </span>
            {#if grantsWriteCount > 0}
              <span class="exposure-line">
                {pageWord(grantsWriteCount)} {verb(grantsWriteCount)} editable by {pending.projectSlug}.
              </span>
            {/if}
          {/if}
          <span class="exposure-line">All history travels with a page.</span>
        </AlertDialog.Description>
        {#if exposedPages.length > 0}
          <div class="exposed">
            <button class="ghost-btn" onclick={() => showExposedPages = !showExposedPages}>
              {showExposedPages ? 'Hide pages' : 'Inspect pages'}
            </button>
            {#if showExposedPages}
              <ul class="exposed-list">
                {#each exposedPages as exposed}
                  <li class="exposed-item">
                    {exposed.slug}
                    <span class="exposed-kinds">{exposed.kinds.join(', ')}</span>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/if}
        {#if confirmError}
          <div class="form-error" role="alert">{confirmError}</div>
        {/if}
        <div class="modal-actions">
          <AlertDialog.Cancel class="alert-btn alert-btn-ghost">Cancel</AlertDialog.Cancel>
          <AlertDialog.Action class="alert-btn alert-btn-primary" onclick={confirmPending}>Confirm</AlertDialog.Action>
        </div>
      {/if}
    </AlertDialog.Content>
  </AlertDialog.Portal>
</AlertDialog.Root>

<style>
  .share-tags {
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    padding: 0.75rem;
  }

  .empty-text {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .tag-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .tag-row {
    display: flex;
    flex-direction: column;
    gap: 0.375rem;
    padding-bottom: 0.75rem;
    border-bottom: 1px solid hsl(var(--border) / 0.6);
  }

  .tag-row:last-child {
    padding-bottom: 0;
    border-bottom: none;
  }

  .tag-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .tag-name {
    font-size: 0.8125rem;
    font-weight: 500;
    font-family: var(--font-mono);
    color: hsl(var(--foreground));
  }

  .member-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .member-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .member-name {
    flex: 1;
    font-size: 0.8125rem;
    color: hsl(var(--foreground));
  }

  .access-select,
  .field-input {
    padding: 0.25rem 0.5rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    outline: none;
  }

  .add-form {
    display: flex;
    flex-direction: column;
    gap: 0.625rem;
  }

  .add-form-title {
    font-size: 0.8125rem;
    font-weight: 500;
    color: hsl(var(--foreground));
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.75rem;
    color: hsl(var(--muted-foreground));
  }

  .add-actions,
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }

  .ghost-btn {
    padding: 0.25rem 0.625rem;
    font-size: 0.75rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: transparent;
    color: hsl(var(--foreground));
    cursor: pointer;
  }

  .ghost-btn:hover {
    background: hsl(var(--muted) / 0.5);
  }

  .primary-btn {
    padding: 0.25rem 0.625rem;
    font-size: 0.75rem;
    border: none;
    border-radius: var(--radius);
    background: hsl(var(--primary));
    color: hsl(var(--primary-foreground));
    cursor: pointer;
  }

  .primary-btn:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .exposure-line {
    display: block;
  }

  .exposed {
    margin-bottom: 1rem;
  }

  .exposed-list {
    list-style: none;
    padding: 0.5rem 0 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.125rem;
    max-height: 200px;
    overflow-y: auto;
  }

  .exposed-item {
    font-size: 0.75rem;
    font-family: var(--font-mono);
    color: hsl(var(--muted-foreground));
  }

  .exposed-kinds {
    margin-left: 0.5rem;
    font-family: var(--font-sans);
    font-style: italic;
    opacity: 0.75;
  }

  .form-error {
    padding: 0.375rem 0.625rem;
    border-radius: var(--radius-sm);
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
  }
</style>
