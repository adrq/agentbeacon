<script lang="ts">
  import { get } from 'svelte/store';
  import { agentDetailQuery, createAgentMutation, updateAgentMutation, driversQuery } from '../queries/agents';
  import { agentFormPrefill } from '../stores/appState';
  import { router } from '../router';
  import Button from './ui/button.svelte';

  interface Props {
    agentId?: string;
  }

  let { agentId }: Props = $props();

  const isEdit = !!agentId;
  const agentQuery = agentDetailQuery(() => agentId ?? null);
  const createMut = createAgentMutation();
  const updateMut = updateAgentMutation();
  const drivers = driversQuery();

  let name = $state('');
  let description = $state('');
  let systemPrompt = $state('');
  let selectedDriverId = $state('');
  let configText = $state('{}');
  let sandboxConfigText = $state('');
  let error: string | null = $state(null);
  let configError: string | null = $state(null);
  let initialized = false;
  let prefillApplied = false;

  // Platform labels for display
  const platformLabels: Record<string, string> = {
    claude_sdk: 'Claude SDK',
    codex_sdk: 'Codex SDK',
    copilot_sdk: 'Copilot SDK',
    opencode_sdk: 'OpenCode SDK',
    acp: 'ACP',
    a2a: 'A2A',
  };

  // Populate from fetched agent data (edit mode)
  $effect(() => {
    const agent = agentQuery.data;
    if (agent && !initialized) {
      initialized = true;
      name = agent.name;
      description = agent.description ?? '';
      systemPrompt = agent.system_prompt ?? '';
      selectedDriverId = agent.driver_id ?? '';
      configText = JSON.stringify(agent.config ?? {}, null, 2);
      sandboxConfigText = agent.sandbox_config ? JSON.stringify(agent.sandbox_config, null, 2) : '';
    }
  });

  // Apply prefill store (create mode with template)
  $effect.pre(() => {
    if (!isEdit && !prefillApplied) {
      const prefill = get(agentFormPrefill);
      if (prefill) {
        prefillApplied = true;
        if (prefill.template) {
          name = prefill.template.name ?? '';
          description = prefill.template.description ?? '';
          configText = JSON.stringify(prefill.template.config ?? {}, null, 2);
        }
        if (prefill.driverId) selectedDriverId = prefill.driverId;
        agentFormPrefill.set(null);
      }
    }
  });

  // Track initial values for dirty detection (edit mode)
  let initialName = $derived(agentQuery.data?.name ?? '');
  let initialDesc = $derived(agentQuery.data?.description ?? '');
  let initialSysPrompt = $derived(agentQuery.data?.system_prompt ?? '');
  let initialConfig = $derived(JSON.stringify(agentQuery.data?.config ?? {}, null, 2));
  let initialSandbox = $derived(agentQuery.data?.sandbox_config ? JSON.stringify(agentQuery.data.sandbox_config, null, 2) : '');

  let isDirty = $derived(
    isEdit
      ? (name !== initialName || description !== initialDesc || systemPrompt !== initialSysPrompt ||
         configText !== initialConfig || sandboxConfigText !== initialSandbox)
      : (name.trim().length > 0 || description.trim().length > 0 || systemPrompt.trim().length > 0 ||
         selectedDriverId !== '' || configText !== '{}' || sandboxConfigText.trim().length > 0)
  );

  let submitting = $derived(createMut.isPending || updateMut.isPending);

  function parseJSON(text: string): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        return parsed;
      }
      return null;
    } catch {
      return null;
    }
  }

  let canSubmit = $derived.by(() => {
    if (!name.trim() || submitting) return false;
    if (!isEdit && !selectedDriverId) return false;
    const config = parseJSON(configText);
    if (!config) return false;
    if (sandboxConfigText.trim() && !parseJSON(sandboxConfigText)) return false;
    return true;
  });

  function validateConfig() {
    if (!configText.trim()) {
      configError = 'Config is required';
      return;
    }
    const config = parseJSON(configText);
    if (!config) {
      configError = 'Invalid JSON object';
      return;
    }
    configError = null;
  }

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
    validateConfig();
    if (configError) return;

    const config = parseJSON(configText)!;
    const sandboxConfig = sandboxConfigText.trim() ? parseJSON(sandboxConfigText) : null;

    try {
      if (isEdit && agentId) {
        const agent = agentQuery.data!;
        const req: Record<string, unknown> = {};
        if (name.trim() !== agent.name) req.name = name.trim();
        const newDesc = description.trim() || null;
        if (newDesc !== (agent.description ?? null)) req.description = newDesc;
        const newSysPrompt = systemPrompt.trim() || null;
        if (newSysPrompt !== (agent.system_prompt ?? null)) req.system_prompt = newSysPrompt;
        if (JSON.stringify(config) !== JSON.stringify(agent.config)) req.config = config;
        const oldSandbox = agent.sandbox_config ? JSON.stringify(agent.sandbox_config) : null;
        const newSandbox = sandboxConfig ? JSON.stringify(sandboxConfig) : null;
        if (newSandbox !== oldSandbox) req.sandbox_config = sandboxConfig;
        await updateMut.mutateAsync({ id: agentId, req });
        clearGuard();
        router.navigate(`/agents/${agentId}`);
      } else {
        const result = await createMut.mutateAsync({
          name: name.trim(),
          description: description.trim() || null,
          driver_id: selectedDriverId,
          config,
          sandbox_config: sandboxConfig,
          system_prompt: systemPrompt.trim() || null,
        });
        clearGuard();
        router.navigate(`/agents/${result.id}`);
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to save agent';
    }
  }

  function handleCancel() {
    clearGuard();
    if (isEdit && agentId) {
      router.navigate(`/agents/${agentId}`);
    } else {
      router.navigate('/agents');
    }
  }

  function driverLabel(d: { name: string; platform: string }): string {
    return `${d.name} (${platformLabels[d.platform] ?? d.platform})`;
  }
</script>

<div class="form-panel scroll-thin">
  <div class="form-panel-header">
    <h2 class="form-panel-title">{isEdit ? 'Edit Agent' : 'Add Agent'}</h2>
    <div class="form-panel-actions">
      <Button variant="ghost" onclick={handleCancel}>Cancel</Button>
      <Button variant="default" disabled={!canSubmit} onclick={handleSubmit}>
        {submitting ? 'Saving...' : isEdit ? 'Save' : 'Add'}
      </Button>
    </div>
  </div>

  {#if isEdit && agentQuery.isLoading}
    <div class="form-loading">Loading agent...</div>
  {:else if isEdit && agentQuery.isError}
    <div class="form-error-state">{agentQuery.error?.message ?? 'Failed to load agent'}</div>
  {:else}
    <div class="field">
      <label class="field-label" for="agent-name">Name</label>
      <input
        id="agent-name"
        class="field-input"
        type="text"
        placeholder="My Agent"
        bind:value={name}
      />
    </div>

    <div class="field">
      <label class="field-label" for="agent-description">Description <span class="optional">(optional)</span></label>
      <input
        id="agent-description"
        class="field-input"
        type="text"
        placeholder="What this agent does"
        bind:value={description}
      />
    </div>

    <div class="field">
      <label class="field-label" for="agent-driver-select">Driver</label>
      <select
        id="agent-driver-select"
        class="field-select"
        bind:value={selectedDriverId}
        disabled={isEdit}
      >
        <option value="" disabled>Select a driver...</option>
        {#each drivers.data ?? [] as d}
          <option value={d.id}>{driverLabel(d)}</option>
        {/each}
      </select>
      {#if isEdit}
        <span class="field-hint">Driver cannot be changed after creation.</span>
      {/if}
    </div>

    <div class="field">
      <label class="field-label" for="agent-config">Config (JSON)</label>
      <textarea
        id="agent-config"
        class="field-textarea mono"
        bind:value={configText}
        onblur={validateConfig}
        rows="6"
      ></textarea>
      {#if configError}
        <span class="field-error">{configError}</span>
      {/if}
    </div>

    <hr class="form-section-divider" />
    <div class="form-section-label">Advanced</div>

    <div class="field">
      <label class="field-label" for="agent-system-prompt">System Prompt <span class="optional">(optional)</span></label>
      <textarea
        id="agent-system-prompt"
        class="field-textarea"
        bind:value={systemPrompt}
        rows="4"
        placeholder="Custom instructions for this agent..."
      ></textarea>
      <span class="field-hint">Prepended to agent's context at execution start.</span>
    </div>

    <div class="field">
      <label class="field-label" for="agent-sandbox">Sandbox Config (JSON) <span class="optional">(optional)</span></label>
      <textarea
        id="agent-sandbox"
        class="field-textarea mono"
        bind:value={sandboxConfigText}
        rows="4"
      ></textarea>
    </div>

    {#if error}
      <div class="form-error" role="alert">{error}</div>
    {/if}
  {/if}
</div>
