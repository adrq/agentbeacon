<script lang="ts">
  import { onMount } from 'svelte';
  import type { Agent } from '../types';
  import { api } from '../api';
  import { router } from '../router';
  import Button from './ui/button.svelte';

  interface Props {
    onclose?: () => void;
  }

  let { onclose }: Props = $props();

  let agents: Agent[] = $state([]);
  let selectedAgentId = $state('');
  let task = $state('');
  let title = $state('');
  let submitting = $state(false);
  let error: string | null = $state(null);

  let enabledAgents = $derived(agents.filter(a => a.enabled));
  let canSubmit = $derived(!!selectedAgentId && task.trim().length > 0 && !submitting);

  let agentSelectEl: HTMLSelectElement;

  onMount(async () => {
    try {
      agents = await api.getAgents();
      const enabled = agents.filter(a => a.enabled);
      if (enabled.length === 1) {
        selectedAgentId = enabled[0].id;
      }
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to load agents';
    }
    agentSelectEl?.focus();
  });

  async function handleSubmit() {
    if (!canSubmit) return;
    submitting = true;
    error = null;

    try {
      const prompt = task.trim();
      const result = await api.createExecution({
        agent_id: selectedAgentId,
        prompt,
        title: title.trim() || generateTitle(prompt),
      });
      onclose?.();
      router.navigate(`/execution/${result.execution_id}`);
    } catch (e) {
      error = e instanceof Error ? e.message : 'Failed to create execution';
    } finally {
      submitting = false;
    }
  }

  function generateTitle(prompt: string): string {
    const firstLine = prompt.split('\n')[0].trim();
    // Cut at first sentence-ending punctuation
    const match = firstLine.match(/^[^.!?]+[.!?]/);
    const text = match ? match[0] : firstLine;
    if (text.length <= 60) return text;
    const truncated = text.slice(0, 60);
    const lastSpace = truncated.lastIndexOf(' ');
    return lastSpace > 0 ? truncated.slice(0, lastSpace) + '...' : truncated + '...';
  }

  function handleBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) onclose?.();
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') onclose?.();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
<div class="modal-backdrop" onclick={handleBackdropClick}>
  <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="modal-heading">
    <h2 class="modal-title" id="modal-heading">New Execution</h2>

    <div class="field">
      <label class="field-label" for="agent-select">Agent</label>
      <select
        id="agent-select"
        class="field-select"
        bind:value={selectedAgentId}
        bind:this={agentSelectEl}
      >
        <option value="">Select an agent...</option>
        {#each enabledAgents as agent}
          <option value={agent.id}>{agent.name}</option>
        {/each}
      </select>
    </div>

    <div class="field">
      <label class="field-label" for="task-input">Task</label>
      <textarea
        id="task-input"
        class="field-textarea"
        placeholder="Describe what the agent should do..."
        bind:value={task}
        rows="4"
      ></textarea>
    </div>

    <div class="field">
      <label class="field-label" for="title-input">Title <span class="optional">(optional)</span></label>
      <input
        id="title-input"
        class="field-input"
        type="text"
        placeholder="Short title for this execution"
        bind:value={title}
      />
    </div>

    {#if error}
      <div class="modal-error">{error}</div>
    {/if}

    <div class="modal-actions">
      <Button variant="ghost" onclick={() => onclose?.()}>Cancel</Button>
      <Button variant="default" disabled={!canSubmit} onclick={handleSubmit}>
        {submitting ? 'Starting...' : 'Start'}
      </Button>
    </div>
  </div>
</div>

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
    backdrop-filter: blur(2px);
  }

  .modal-panel {
    background: hsl(var(--card));
    border: 1px solid hsl(var(--border));
    border-radius: 0.5rem;
    padding: 1.5rem;
    width: 100%;
    max-width: 28rem;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
  }

  .modal-title {
    font-size: 1.0625rem;
    font-weight: 600;
    margin-bottom: 1.25rem;
    color: hsl(var(--foreground));
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

  .field-textarea {
    resize: vertical;
    min-height: 5rem;
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
