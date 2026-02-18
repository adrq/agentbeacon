<script lang="ts">
  import { Dialog } from 'bits-ui';
  import type { Agent, AgentType } from '../types';
  import { createAgentMutation, updateAgentMutation } from '../queries/agents';
  import Button from './ui/button.svelte';

  interface AgentTemplate {
    name: string;
    agent_type: string;
    description: string;
    config: Record<string, unknown>;
  }

  interface Props {
    agent?: Agent | null;
    template?: AgentTemplate | null;
    onsubmit?: () => void;
    oncancel?: () => void;
  }

  let { agent = null, template = null, onsubmit, oncancel }: Props = $props();

  const createMut = createAgentMutation();
  const updateMut = updateAgentMutation();

  let isOpen = $state(true);
  let isEdit = $derived(!!agent);

  let name = $state(agent?.name ?? template?.name ?? '');
  let description = $state(agent?.description ?? template?.description ?? '');
  let agentType = $state<string>(agent?.agent_type ?? template?.agent_type ?? 'claude_sdk');
  let configText = $state(JSON.stringify(agent?.config ?? template?.config ?? {}, null, 2));
  let sandboxConfigText = $state(
    agent?.sandbox_config ? JSON.stringify(agent.sandbox_config, null, 2) : ''
  );
  let showSandbox = $state(!!agent?.sandbox_config);
  let error: string | null = $state(null);
  let configError: string | null = $state(null);

  let submitting = $derived(createMut.isPending || updateMut.isPending);

  const agentTypes: { value: AgentType; label: string }[] = [
    { value: 'claude_sdk', label: 'Claude SDK' },
    { value: 'codex_sdk', label: 'Codex SDK' },
    { value: 'copilot_sdk', label: 'Copilot SDK' },
    { value: 'opencode_sdk', label: 'OpenCode SDK' },
    { value: 'acp', label: 'ACP' },
    { value: 'a2a', label: 'A2A' },
  ];

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

  async function handleSubmit() {
    if (!canSubmit) return;
    error = null;
    validateConfig();
    if (configError) return;

    const config = parseJSON(configText)!;
    const sandboxConfig = sandboxConfigText.trim() ? parseJSON(sandboxConfigText) : null;

    try {
      if (isEdit && agent) {
        const req: Record<string, unknown> = {};
        if (name.trim() !== agent.name) req.name = name.trim();
        const newDesc = description.trim() || null;
        if (newDesc !== (agent.description ?? null)) req.description = newDesc;
        if (JSON.stringify(config) !== JSON.stringify(agent.config)) req.config = config;
        const oldSandbox = agent.sandbox_config ? JSON.stringify(agent.sandbox_config) : null;
        const newSandbox = sandboxConfig ? JSON.stringify(sandboxConfig) : null;
        if (newSandbox !== oldSandbox) req.sandbox_config = sandboxConfig;
        await updateMut.mutateAsync({ id: agent.id, req });
      } else {
        await createMut.mutateAsync({
          name: name.trim(),
          description: description.trim() || null,
          agent_type: agentType as AgentType,
          config,
          sandbox_config: sandboxConfig,
        });
      }
      onsubmit?.();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to save agent';
    }
  }

  function handleClose() {
    isOpen = false;
    oncancel?.();
  }
</script>

<Dialog.Root bind:open={isOpen} onOpenChange={(o) => { if (!o) handleClose(); }}>
  <Dialog.Portal>
    <Dialog.Overlay class="modal-overlay" />
    <Dialog.Content class="modal-content modal-content-wide" aria-describedby={undefined}>
      <Dialog.Title class="modal-title">{isEdit ? 'Edit Agent' : 'Add Agent'}</Dialog.Title>

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
        <label class="field-label" for="agent-type-select">Agent Type</label>
        <select
          id="agent-type-select"
          class="field-select"
          bind:value={agentType}
          disabled={isEdit}
        >
          {#each agentTypes as t}
            <option value={t.value}>{t.label}</option>
          {/each}
        </select>
        {#if isEdit}
          <span class="field-hint">Agent type cannot be changed after creation.</span>
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

      <button class="toggle-link" onclick={() => showSandbox = !showSandbox}>
        {showSandbox ? 'Hide' : 'Show'} Sandbox Config
      </button>

      {#if showSandbox}
        <div class="field">
          <label class="field-label" for="agent-sandbox">Sandbox Config (JSON) <span class="optional">(optional)</span></label>
          <textarea
            id="agent-sandbox"
            class="field-textarea mono"
            bind:value={sandboxConfigText}
            rows="4"
          ></textarea>
        </div>
      {/if}

      {#if error}
        <div class="modal-error">{error}</div>
      {/if}

      <div class="modal-actions">
        <Button variant="ghost" onclick={handleClose}>Cancel</Button>
        <Button variant="default" disabled={!canSubmit} onclick={handleSubmit}>
          {submitting ? 'Saving...' : isEdit ? 'Save' : 'Add'}
        </Button>
      </div>
    </Dialog.Content>
  </Dialog.Portal>
</Dialog.Root>

<style>
  :global(.modal-content-wide) {
    max-width: 32rem !important;
  }

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

  .optional {
    color: hsl(var(--muted-foreground));
    font-weight: 400;
  }

  .field-select, .field-textarea, .field-input {
    width: 100%;
    padding: 0.5rem 0.625rem;
    border: 1px solid hsl(var(--border));
    border-radius: 0.375rem;
    background: hsl(var(--background));
    color: hsl(var(--foreground));
    font-size: 0.8125rem;
    font-family: inherit;
  }

  .field-select:focus, .field-textarea:focus, .field-input:focus {
    outline: none;
    border-color: hsl(var(--primary));
    box-shadow: 0 0 0 2px hsl(var(--primary) / 0.15);
  }

  .field-select:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .field-textarea {
    resize: vertical;
    min-height: 4rem;
  }

  .field-textarea.mono {
    font-family: var(--font-mono);
    font-size: 0.75rem;
    line-height: 1.5;
  }

  .field-hint {
    display: block;
    font-size: 0.6875rem;
    color: hsl(var(--muted-foreground));
    margin-top: 0.25rem;
  }

  .field-error {
    display: block;
    font-size: 0.6875rem;
    color: hsl(var(--status-danger));
    margin-top: 0.25rem;
  }

  .toggle-link {
    background: none;
    border: none;
    font-size: 0.75rem;
    color: hsl(var(--primary));
    cursor: pointer;
    padding: 0;
    margin-bottom: 0.75rem;
  }

  .toggle-link:hover {
    opacity: 0.8;
  }

  .modal-error {
    padding: 0.375rem 0.625rem;
    border-radius: 0.25rem;
    background: hsl(var(--status-danger) / 0.1);
    color: hsl(var(--status-danger));
    font-size: 0.8125rem;
    margin-bottom: 1rem;
  }

  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
  }
</style>
