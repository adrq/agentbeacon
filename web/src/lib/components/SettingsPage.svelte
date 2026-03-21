<script lang="ts">
  import { configQuery, updateConfigMutation } from '../queries/config';
  import type { ConfigEntry } from '../types';
  import Button from './ui/button.svelte';
  import McpServersSection from './McpServersSection.svelte';
  import { router } from '../router';

  const config = configQuery();
  const updateMut = updateConfigMutation();

  // Track dirty state per config key
  let editValues = $state<Map<string, string>>(new Map());
  let saveStatus = $state<Map<string, 'saved' | 'error'>>(new Map());
  let savingKeys = $state<Set<string>>(new Set());

  // Active sidebar nav section (updated by IntersectionObserver scroll-spy)
  let activeNavSection = $state<string>('briefing-templates');
  let contentEl = $state<HTMLElement | null>(null);

  // Only show briefing.* entries in the Briefing Templates section
  let entries = $derived<ConfigEntry[]>((config.data ?? []).filter(e => e.name.startsWith('briefing.')));

  // Navigation guard — warns when there are unsaved briefing edits
  let hasUnsavedEdits = $derived(editValues.size > 0);
  let guardCleanup: (() => void) | null = null;
  let unloadHandler: ((e: BeforeUnloadEvent) => void) | null = null;
  $effect(() => {
    if (hasUnsavedEdits) {
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

  // Scroll-spy: update activeNavSection based on which section is topmost in view.
  // We track every section's intersecting state so that when multiple sections are
  // simultaneously visible we always activate the topmost one rather than whichever
  // entry happened to be last in the callback array.
  $effect(() => {
    const root = contentEl;
    if (!root) return;
    const sectionIds = ['briefing-templates', 'integrations'];
    const intersecting = new Map<string, boolean>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          intersecting.set(entry.target.id, entry.isIntersecting);
        }
        // Among all currently-visible sections, activate the topmost one
        let topmostId: string | null = null;
        let topmostTop = Infinity;
        for (const id of sectionIds) {
          if (!intersecting.get(id)) continue;
          const el = root.querySelector(`#${id}`);
          if (!el) continue;
          const top = el.getBoundingClientRect().top;
          if (top < topmostTop) {
            topmostTop = top;
            topmostId = id;
          }
        }
        if (topmostId) activeNavSection = topmostId;
      },
      { root, threshold: 0.1, rootMargin: '0px 0px -60% 0px' },
    );
    for (const id of sectionIds) {
      const el = root.querySelector(`#${id}`);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  });

  // Briefing keys shown with friendly labels
  const keyLabels: Record<string, string> = {
    'briefing.delegation': 'Delegation Briefing',
    'briefing.escalate': 'Escalate Briefing',
    'briefing.rest_api': 'REST API Briefing',
  };

  function getDisplayLabel(name: string): string {
    return keyLabels[name] ?? name;
  }

  function getCurrentValue(entry: ConfigEntry): string {
    return editValues.get(entry.name) ?? entry.value;
  }

  function isDirty(entry: ConfigEntry): boolean {
    const edited = editValues.get(entry.name);
    return edited !== undefined && edited !== entry.value;
  }

  function handleInput(entry: ConfigEntry, value: string) {
    const next = new Map(editValues);
    if (value === entry.value) {
      next.delete(entry.name);
    } else {
      next.set(entry.name, value);
    }
    editValues = next;
    // Clear save status when editing
    if (saveStatus.has(entry.name)) {
      const s = new Map(saveStatus);
      s.delete(entry.name);
      saveStatus = s;
    }
  }

  async function handleSave(name: string) {
    const value = editValues.get(name);
    if (value === undefined) return;

    savingKeys = new Set(savingKeys).add(name);
    try {
      await updateMut.mutateAsync({ name, value });
      // Clear edit state on success
      const nextEdit = new Map(editValues);
      nextEdit.delete(name);
      editValues = nextEdit;
      saveStatus = new Map(saveStatus).set(name, 'saved');
      setTimeout(() => {
        if (saveStatus.get(name) === 'saved') {
          const next = new Map(saveStatus);
          next.delete(name);
          saveStatus = next;
        }
      }, 4000);
    } catch {
      saveStatus = new Map(saveStatus).set(name, 'error');
    } finally {
      const next = new Set(savingKeys);
      next.delete(name);
      savingKeys = next;
    }
  }

  function scrollToSection(sectionId: string) {
    activeNavSection = sectionId;
    contentEl?.querySelector(`#${sectionId}`)?.scrollIntoView({ behavior: 'smooth' });
  }

  function handleTextareaKeydown(e: KeyboardEvent, name: string) {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      handleSave(name);
    }
  }
</script>

<div class="settings-page">
  <nav class="settings-sidebar" aria-label="Settings sections">
    <button
      class="sidebar-item"
      class:active={activeNavSection === 'briefing-templates'}
      aria-current={activeNavSection === 'briefing-templates' ? 'location' : undefined}
      onclick={() => scrollToSection('briefing-templates')}
    >
      Briefing Templates
    </button>
    <button
      class="sidebar-item"
      class:active={activeNavSection === 'integrations'}
      aria-current={activeNavSection === 'integrations' ? 'location' : undefined}
      onclick={() => scrollToSection('integrations')}
    >
      Integrations
    </button>
  </nav>

  <div class="settings-content scroll-thin" bind:this={contentEl}>
    <h2 class="settings-title">Settings</h2>

    <section id="briefing-templates" class="settings-section">
      <h3 class="section-heading">Briefing Templates</h3>
      <p class="section-description">Configure the system prompt snippets injected into agent sessions.</p>

      {#if config.isLoading}
        <p class="settings-loading">Loading configuration...</p>
      {:else if config.isError}
        <p class="settings-error">{config.error?.message ?? 'Failed to load config'}</p>
      {:else if entries.length === 0}
        <p class="settings-empty">No configuration entries. Config will appear here once seeded.</p>
      {:else}
        <div class="settings-entries">
          {#each entries as entry (entry.name)}
            <div class="settings-entry">
              <label class="settings-label" for="config-{entry.name}">{getDisplayLabel(entry.name)}</label>
              <textarea
                id="config-{entry.name}"
                class="settings-textarea"
                rows="6"
                value={getCurrentValue(entry)}
                oninput={(e) => handleInput(entry, e.currentTarget.value)}
                onkeydown={(e) => handleTextareaKeydown(e, entry.name)}
              ></textarea>
              <div class="settings-entry-footer">
                {#if saveStatus.get(entry.name) === 'saved'}
                  <span class="save-success" role="status" aria-live="polite">Saved</span>
                {:else if saveStatus.get(entry.name) === 'error'}
                  <span class="save-error" role="status" aria-live="polite">Save failed</span>
                {:else}
                  <span class="save-placeholder" role="status" aria-live="polite"></span>
                {/if}
                <Button
                  variant="default"
                  size="sm"
                  disabled={!isDirty(entry) || savingKeys.has(entry.name)}
                  onclick={() => handleSave(entry.name)}
                >
                  {savingKeys.has(entry.name) ? 'Saving...' : 'Save'}
                </Button>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </section>

    <section id="integrations" class="settings-section">
      <h3 class="section-heading">Integrations</h3>
      <p class="section-description">Manage external tool server connections.</p>
      <McpServersSection />
    </section>
  </div>
</div>

<style>
  .settings-page {
    flex: 1;
    display: flex;
    min-height: 0;
    overflow: hidden;
  }

  .settings-sidebar {
    width: 220px;
    flex: 0 0 220px;
    display: flex;
    flex-direction: column;
    padding: 1.5rem 0.75rem;
    gap: 0.125rem;
    border-right: 1px solid hsl(var(--border));
    background: hsl(var(--background));
    overflow-y: auto;
  }

  .sidebar-item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.375rem 0.625rem;
    border: none;
    border-radius: var(--radius);
    background: transparent;
    color: hsl(var(--muted-foreground));
    font-size: 0.8125rem;
    font-weight: 400;
    cursor: pointer;
    transition: color 0.15s, background 0.15s;
  }

  .sidebar-item:hover {
    color: hsl(var(--foreground));
    background: hsl(var(--muted) / 0.5);
  }

  .sidebar-item.active {
    color: hsl(var(--primary));
    font-weight: 500;
    background: hsl(var(--primary) / 0.08);
  }

  .settings-content {
    flex: 1;
    overflow-y: auto;
    padding: 1.5rem 2rem;
    min-width: 0;
  }

  .settings-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    margin-bottom: 1.5rem;
  }

  .settings-section {
    max-width: 720px;
    margin-bottom: 3rem;
  }

  .section-heading {
    font-size: 0.875rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    margin-bottom: 0.25rem;
  }

  .section-description {
    font-size: 0.8125rem;
    font-weight: 400;
    color: hsl(var(--muted-foreground));
    margin-bottom: 1rem;
  }

  .settings-loading, .settings-empty {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .settings-error {
    font-size: 0.8125rem;
    color: hsl(var(--status-danger));
  }

  .settings-entries {
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
  }

  .settings-entry {
    padding: 1rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--card));
  }

  .settings-label {
    display: block;
    font-size: 0.8125rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    margin-bottom: 0.5rem;
  }

  .settings-textarea {
    width: 100%;
    padding: 0.5rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: var(--font-mono);
    line-height: 1.5;
    resize: vertical;
    min-height: 4rem;
  }

  .settings-textarea:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .settings-entry-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.5rem;
  }

  .save-success {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--status-success));
  }

  .save-error {
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--status-danger));
  }

  .save-placeholder {
    flex: 1;
  }

  @media (max-width: 768px) {
    .settings-page {
      flex-direction: column;
    }

    .settings-sidebar {
      display: none;
    }

    .settings-content {
      padding: 1rem;
    }

    .section-heading {
      position: sticky;
      top: 0;
      background: hsl(var(--background));
      padding: 0.5rem 0;
      z-index: 10;
    }
  }
</style>
