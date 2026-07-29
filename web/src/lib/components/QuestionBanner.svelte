<script lang="ts">
  import type { Execution } from '../types';
  import { pendingDecisions } from '../stores/questionState';
  import { actionPanelCollapsed, userExplicitlyCollapsed } from '../stores/appState';

  interface Props {
    execution: Execution;
  }

  let { execution }: Props = $props();

  let execPending = $derived($pendingDecisions.filter(d => d.executionId === execution.id));
  let pendingCount = $derived(execPending.length);

  function openDecisionsPanel() {
    actionPanelCollapsed.set(false);
    userExplicitlyCollapsed.set(false);
  }
</script>

{#if pendingCount > 0}
  <button type="button" class="question-notification" onclick={openDecisionsPanel}>
    <span class="notif-icon" aria-hidden="true">&#x26A0;</span>
    <span class="notif-text">
      {pendingCount} question{pendingCount !== 1 ? 's' : ''} pending
    </span>
    <span class="notif-action">Open Decisions &rarr;</span>
  </button>
{/if}

<style>
  .question-notification {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.375rem 0.75rem;
    padding: 0.375rem 0.75rem;
    border: 1.5px solid hsl(var(--status-attention) / 0.35);
    border-radius: var(--radius);
    background: hsl(var(--status-attention) / 0.08);
    width: calc(100% - 1.5rem);
    text-align: left;
    font: inherit;
    color: inherit;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }

  .question-notification:hover {
    background: hsl(var(--status-attention) / 0.15);
    border-color: hsl(var(--status-attention) / 0.5);
  }

  .notif-icon {
    color: hsl(var(--status-attention));
    font-size: 0.8125rem;
    flex-shrink: 0;
  }

  .notif-text {
    font-size: 0.75rem;
    font-weight: 600;
    color: hsl(var(--status-attention));
  }

  .notif-action {
    margin-left: auto;
    font-size: 0.6875rem;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
  }

  .notif-action:hover {
    color: hsl(var(--foreground));
  }
</style>
