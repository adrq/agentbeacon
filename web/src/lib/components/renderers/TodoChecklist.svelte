<script lang="ts">
  import type { TodoItem } from '../../types';
  import { statusIcon, todoCounts } from '../todo-helpers';

  interface Props {
    todos: TodoItem[];
  }

  let { todos }: Props = $props();
  let expanded = $state(false);

  const bodyId = `todo-checklist-body-${crypto.randomUUID().slice(0, 8)}`;

  let counts = $derived(todoCounts(todos));
</script>

<div class="todo-checklist" class:expanded>
  <button
    class="todo-summary"
    aria-expanded={expanded}
    aria-controls={bodyId}
    onclick={() => expanded = !expanded}
  >
    <span class="summary-chevron" class:open={expanded}>&#x25B8;</span>
    <span class="summary-text">Todo list updated</span>
    <span class="summary-counts">
      {#if counts.inProgress > 0}
        <span class="count-working">{counts.inProgress} active</span>
        <span class="count-sep">&middot;</span>
      {/if}
      <span class="count-done">{counts.completed}/{counts.total} done</span>
    </span>
  </button>
  <div class="todo-items" id={bodyId} hidden={!expanded}>
    {#each todos as item}
      <div class="todo-item {item.status}">
        <span class="todo-icon {item.status}">{statusIcon(item.status)}</span>
        <span class="todo-content">{item.content}</span>
      </div>
    {/each}
  </div>
</div>

<style>
  .todo-checklist {
    border: 1px solid hsl(var(--border));
    border-radius: var(--radius);
    background: hsl(var(--muted) / 0.15);
    overflow: hidden;
    max-width: 85%;
  }

  .todo-summary {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    width: 100%;
    padding: 0.375rem 0.625rem;
    font-size: 0.6875rem;
    cursor: pointer;
    border: none;
    background: none;
    text-align: left;
    font: inherit;
    color: inherit;
    appearance: none;
    -webkit-appearance: none;
  }

  .todo-summary:focus-visible {
    outline: 2px solid hsl(var(--ring));
    outline-offset: -2px;
  }

  .todo-summary:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .summary-chevron {
    font-size: 0.5rem;
    transition: transform 0.15s ease;
    display: inline-block;
    color: hsl(var(--muted-foreground));
    flex-shrink: 0;
  }

  .summary-chevron.open {
    transform: rotate(90deg);
  }

  .summary-text {
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .summary-counts {
    margin-left: auto;
    font-weight: 500;
    font-size: 0.625rem;
    font-family: var(--font-mono);
  }

  .count-working { color: hsl(var(--status-working)); }
  .count-done { color: hsl(var(--muted-foreground)); }
  .count-sep { opacity: 0.4; margin: 0 0.25rem; }

  .todo-items {
    padding: 0.25rem 0;
    border-top: 1px solid hsl(var(--border) / 0.5);
  }

  .todo-item {
    display: flex;
    align-items: flex-start;
    gap: 0.375rem;
    padding: 0.1875rem 0.625rem;
    font-size: 0.75rem;
    line-height: 1.4;
  }

  .todo-icon {
    flex-shrink: 0;
    font-size: 0.625rem;
    line-height: 1.4;
    width: 0.75rem;
    text-align: center;
  }

  .todo-icon.completed { color: hsl(var(--status-success)); }
  .todo-icon.in_progress { color: hsl(var(--status-working)); }
  .todo-icon.pending { color: hsl(var(--muted-foreground)); }

  .todo-content {
    color: hsl(var(--foreground));
    word-break: break-word;
  }

  .todo-item.completed .todo-content {
    text-decoration: line-through;
    color: hsl(var(--muted-foreground));
  }
</style>
