<!-- SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga -->
<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->

<script lang="ts">
  import type { Execution } from '../types';
  import { pendingDecisions } from '../stores/questionState';
  import { openDecisionsPanel } from '../stores/appState';

  interface Props {
    execution: Execution;
  }

  let { execution }: Props = $props();

  let execPending = $derived($pendingDecisions.filter(d => d.executionId === execution.id));
  let pendingCount = $derived(execPending.length);
</script>

{#if pendingCount > 0}
  <button
    type="button"
    class="question-notification"
    onclick={openDecisionsPanel}
    title="Open Decisions"
    aria-label="{pendingCount} pending question{pendingCount !== 1 ? 's' : ''}, open Decisions"
  >
    <span class="notif-icon" aria-hidden="true">&#x26A0;</span>
    <span class="notif-text" aria-hidden="true">
      {pendingCount} question{pendingCount !== 1 ? 's' : ''}
    </span>
    <span class="notif-action" aria-hidden="true">&rarr;</span>
    <!-- Mobile: the sidebar's per-execution marker, uncounted so it can't be read against the global inbox badge. -->
    <span class="notif-mark" aria-hidden="true">!</span>
  </button>
{/if}

<style>
  /* Sits in the execution header beside the status badge. */
  .question-notification {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    flex-shrink: 0;
    padding: 0.25rem 0.625rem;
    border: 1px solid hsl(var(--status-attention) / 0.35);
    border-radius: var(--radius-sm);
    background: hsl(var(--status-attention) / 0.08);
    font: inherit;
    color: hsl(var(--status-attention));
    white-space: nowrap;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }

  .question-notification:hover {
    background: hsl(var(--status-attention) / 0.15);
    border-color: hsl(var(--status-attention) / 0.5);
  }

  .notif-icon,
  .notif-text,
  .notif-action {
    font-size: 0.6875rem;
    font-weight: 600;
  }

  .notif-mark {
    display: none;
  }

  @media (max-width: 768px) {
    .question-notification {
      justify-content: center;
      min-width: 44px;
      min-height: 44px;
      margin: -0.5rem 0;
      padding: 0;
      border: none;
      background: transparent;
    }
    .question-notification:hover {
      background: transparent;
    }
    .notif-icon,
    .notif-text,
    .notif-action {
      display: none;
    }
    .notif-mark {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 1.25rem;
      height: 1.25rem;
      border-radius: 50%;
      background: hsl(var(--status-attention));
      color: hsl(var(--primary-foreground));
      font-size: 0.75rem;
      font-weight: 700;
    }
  }
</style>
