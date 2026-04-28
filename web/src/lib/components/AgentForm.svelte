<script lang="ts">
  import { get } from 'svelte/store';
  import { agentDetailQuery, createAgentMutation, updateAgentMutation, driversQuery, driverDescriptorQuery } from '../queries/agents';
  import { agentFormPrefill } from '../stores/appState';
  import { router } from '../router';
  import Button from './ui/button.svelte';
  import DriverConfigForm from './agent-form/DriverConfigForm.svelte';
  import RawJsonPanel from './agent-form/RawJsonPanel.svelte';

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
  let config: Record<string, unknown> = $state({});
  let error: string | null = $state(null);
  let driverFormHasError = $state(false);
  let rawPanelHasError = $state(false);
  let initialized = false;
  let prefillApplied = false;

  // Platform labels for display
  const platformLabels: Record<string, string> = {
    claude_sdk: 'Claude SDK',
    codex_sdk: 'Codex SDK',
    copilot_sdk: 'Copilot SDK',
    acp: 'ACP',
  };

  // Resolve platform from selected driver, falling back to agent_type for legacy drivers
  const selectedPlatform = $derived.by(() => {
    if (!selectedDriverId) return null;
    const d = (drivers.data ?? []).find(d => d.id === selectedDriverId);
    if (d) return d.platform;
    // Legacy/retired driver not in filtered list — derive from agent record
    if (isEdit && agentQuery.data) return agentQuery.data.agent_type;
    return null;
  });

  const SUPPORTED_PLATFORMS = ['claude_sdk', 'codex_sdk', 'copilot_sdk', 'acp'];
  const isUnsupportedPlatform = $derived(
    selectedPlatform !== null && !SUPPORTED_PLATFORMS.includes(selectedPlatform)
  );

  const descriptorQuery = driverDescriptorQuery(() => selectedPlatform);

  // Reset config when driver changes in create mode
  let prevPlatform: string | null = null;
  $effect(() => {
    const p = selectedPlatform;
    if (!isEdit && prevPlatform !== null && p !== prevPlatform) {
      config = {};
      driverFormHasError = false;
      rawPanelHasError = false;
    }
    prevPlatform = p;
  });

  // Populate from fetched agent data (edit mode)
  $effect(() => {
    const agent = agentQuery.data;
    if (agent && !initialized) {
      initialized = true;
      name = agent.name;
      description = agent.description ?? '';
      systemPrompt = agent.system_prompt ?? '';
      selectedDriverId = agent.driver_id ?? '';
      config = (agent.config && typeof agent.config === 'object' && !Array.isArray(agent.config))
        ? { ...agent.config as Record<string, unknown> }
        : {};
    }
  });

  // Apply prefill store (create mode)
  $effect.pre(() => {
    if (!isEdit && !prefillApplied) {
      const prefill = get(agentFormPrefill);
      if (prefill) {
        prefillApplied = true;
        if (prefill.driverId) selectedDriverId = prefill.driverId;
        agentFormPrefill.set(null);
      }
    }
  });

  // Track initial values for dirty detection (edit mode)
  let initialName = $derived(agentQuery.data?.name ?? '');
  let initialDesc = $derived(agentQuery.data?.description ?? '');
  let initialSysPrompt = $derived(agentQuery.data?.system_prompt ?? '');
  let initialConfig = $derived(JSON.stringify(agentQuery.data?.config ?? {}));
  let isDirty = $derived(
    isEdit
      ? (name !== initialName || description !== initialDesc || systemPrompt !== initialSysPrompt ||
         JSON.stringify(config) !== initialConfig)
      : (name.trim().length > 0 || description.trim().length > 0 || systemPrompt.trim().length > 0 ||
         selectedDriverId !== '' || Object.keys(config).length > 0)
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

  // Block submission when descriptor is expected but not loaded
  const descriptorRequired = $derived(
    selectedDriverId !== '' && !isUnsupportedPlatform && selectedPlatform !== null
  );
  const descriptorReady = $derived(
    !descriptorRequired || !!descriptorQuery.data
  );

  let canSubmit = $derived.by(() => {
    if (!name.trim() || submitting) return false;
    if (!isEdit && !selectedDriverId) return false;
    if (driverFormHasError || rawPanelHasError) return false;
    if (!descriptorReady) return false;
    return true;
  });

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
        await updateMut.mutateAsync({ id: agentId, req });
        clearGuard();
        router.navigate(`/agents/${agentId}`);
      } else {
        const result = await createMut.mutateAsync({
          name: name.trim(),
          description: description.trim() || null,
          driver_id: selectedDriverId,
          config,
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

  function handleConfigChange() {
    // Trigger reactivity — config is mutated in place by DriverConfigForm
    config = { ...config };
  }

  function handleRawJsonChange(newConfig: Record<string, unknown>) {
    config = newConfig;
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

    {#if isUnsupportedPlatform}
      <div class="field">
        <span class="field-hint">This agent uses an unsupported driver ({selectedPlatform}). Config is read-only.</span>
      </div>
      <div class="field">
        <label class="field-label">Config (JSON)</label>
        <textarea
          class="field-textarea mono"
          value={JSON.stringify(config, null, 2)}
          readonly
          rows="6"
        ></textarea>
      </div>
    {:else if descriptorQuery.data}
      <DriverConfigForm
        descriptor={descriptorQuery.data}
        value={config}
        onchange={handleConfigChange}
        onerror={(hasErr) => driverFormHasError = hasErr}
      />
      <RawJsonPanel value={config} onchange={handleRawJsonChange} onerror={(hasErr) => rawPanelHasError = hasErr} />
    {:else if descriptorQuery.isError}
      <div class="field">
        <span class="field-error">Failed to load driver configuration. Please try again.</span>
      </div>
    {:else if selectedDriverId}
      <div class="field">
        <span class="field-hint">Loading driver configuration...</span>
      </div>
    {/if}

    <hr class="form-section-divider" />

    <div class="field">
      <label class="field-label" for="agent-system-prompt">Personality <span class="optional">(optional)</span></label>
      <textarea
        id="agent-system-prompt"
        class="field-textarea"
        bind:value={systemPrompt}
        rows="4"
        placeholder={'e.g. "You\'re an expert code reviewer. Be terse and technical."'}
      ></textarea>
      <span class="field-hint">Appended to the AgentBeacon briefing sent to this agent. Good for role framing; leave blank if the briefing alone is enough.</span>
    </div>

    {#if error}
      <div class="form-error" role="alert">{error}</div>
    {/if}
  {/if}
</div>
