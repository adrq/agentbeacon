<script lang="ts">
  import { configQuery, updateConfigMutation } from '../queries/config';
  import type { ConfigEntry } from '../types';
  import Button from './ui/button.svelte';

  const config = configQuery();
  const updateMut = updateConfigMutation();

  // Track dirty state per config key
  let editValues = $state<Map<string, string>>(new Map());
  let saveStatus = $state<Map<string, 'saved' | 'error'>>(new Map());
  let savingKeys = $state<Set<string>>(new Set());

  let entries = $derived<ConfigEntry[]>(config.data ?? []);

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

  function handleInput(name: string, value: string) {
    editValues = new Map(editValues).set(name, value);
    // Clear save status when editing
    if (saveStatus.has(name)) {
      const next = new Map(saveStatus);
      next.delete(name);
      saveStatus = next;
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
      }, 2000);
    } catch {
      saveStatus = new Map(saveStatus).set(name, 'error');
    } finally {
      const next = new Set(savingKeys);
      next.delete(name);
      savingKeys = next;
    }
  }
</script>

<div class="settings-page scroll-thin">
  <h2 class="settings-title">Settings</h2>

  {#if config.isLoading}
    <p class="settings-loading">Loading configuration...</p>
  {:else if config.isError}
    <p class="settings-error">{config.error?.message ?? 'Failed to load config'}</p>
  {:else if entries.length === 0}
    <p class="settings-empty">No configuration entries. Config will appear here once seeded.</p>
  {:else}
    <div class="settings-sections">
      {#each entries as entry (entry.name)}
        <div class="settings-entry">
          <label class="settings-label" for="config-{entry.name}">{getDisplayLabel(entry.name)}</label>
          <textarea
            id="config-{entry.name}"
            class="settings-textarea"
            rows="6"
            value={getCurrentValue(entry)}
            oninput={(e) => handleInput(entry.name, e.currentTarget.value)}
          ></textarea>
          <div class="settings-entry-footer">
            {#if saveStatus.get(entry.name) === 'saved'}
              <span class="save-success">Saved</span>
            {:else if saveStatus.get(entry.name) === 'error'}
              <span class="save-error">Save failed</span>
            {:else}
              <span class="save-placeholder"></span>
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
</div>

<style>
  .settings-page {
    flex: 1;
    overflow-y: auto;
    padding: 1.5rem 2rem;
    max-width: 48rem;
  }

  .settings-title {
    font-size: 1.25rem;
    font-weight: 600;
    color: hsl(var(--foreground));
    margin-bottom: 1.5rem;
  }

  .settings-loading, .settings-empty {
    font-size: 0.8125rem;
    color: hsl(var(--muted-foreground));
  }

  .settings-error {
    font-size: 0.8125rem;
    color: hsl(var(--status-danger));
  }

  .settings-sections {
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
    color: hsl(var(--status-success));
  }

  .save-error {
    font-size: 0.6875rem;
    color: hsl(var(--status-danger));
  }

  .save-placeholder {
    flex: 1;
  }
</style>
