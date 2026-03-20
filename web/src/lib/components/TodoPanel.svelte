<script lang="ts">
  import type { TodoItem } from '../types';
  import { statusIcon, todoCounts } from './todo-helpers';

  interface Props {
    todos: TodoItem[];
  }

  let { todos }: Props = $props();
  let collapsed = $state(false);

  let counts = $derived(todoCounts(todos));
</script>

<div class="todo-panel">
  <button
    class="todo-panel-header"
    aria-expanded={!collapsed}
    aria-controls="todo-panel-body"
    onclick={() => collapsed = !collapsed}
  >
    <span class="panel-chevron" class:open={!collapsed}>&#x25B8;</span>
    <span class="panel-label">Tasks</span>
    <span class="panel-counts">
      {#if counts.inProgress > 0}
        <span class="count-working">{counts.inProgress} active</span>
        <span class="count-sep">&middot;</span>
      {/if}
      <span class="count-done">{counts.completed}/{counts.total} done</span>
    </span>
  </button>
  <div class="todo-panel-body scroll-thin" id="todo-panel-body" hidden={collapsed}>
    {#each todos as item}
      <div class="panel-item {item.status}">
        <span class="panel-icon {item.status}">{statusIcon(item.status)}</span>
        <span class="panel-content">{item.content}</span>
      </div>
    {/each}
  </div>
</div>

<style>
  .todo-panel {
    flex-shrink: 0;
    border-top: 1px solid hsl(var(--border));
    background: hsl(var(--card));
  }

  .todo-panel-header {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.25rem 1rem;
    cursor: pointer;
    font-size: 0.625rem;
    transition: background 0.1s;
    width: 100%;
    border: none;
    background: none;
    text-align: left;
    font-family: inherit;
    color: inherit;
    appearance: none;
    -webkit-appearance: none;
  }

  .todo-panel-header:focus-visible {
    outline: 2px solid hsl(var(--ring));
    outline-offset: -2px;
  }

  .todo-panel-header:hover {
    background: hsl(var(--muted) / 0.3);
  }

  .panel-chevron {
    font-size: 0.5rem;
    transition: transform 0.15s ease;
    display: inline-block;
    color: hsl(var(--muted-foreground));
  }

  .panel-chevron.open {
    transform: rotate(90deg);
  }

  .panel-label {
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: hsl(var(--muted-foreground));
  }

  .panel-counts {
    margin-left: auto;
    font-weight: 500;
    font-size: 0.625rem;
    font-family: var(--font-mono);
  }

  .count-working { color: hsl(var(--status-working)); }
  .count-done { color: hsl(var(--muted-foreground)); }
  .count-sep { opacity: 0.4; margin: 0 0.25rem; }

  .todo-panel-body {
    max-height: 10rem;
    overflow-y: auto;
    padding: 0.125rem 0 0.25rem;
  }

  .panel-item {
    display: flex;
    align-items: flex-start;
    gap: 0.25rem;
    padding: 0.125rem 1rem 0.125rem 1.5rem;
    font-size: 0.625rem;
    line-height: 1.4;
  }

  .panel-icon {
    flex-shrink: 0;
    font-size: 0.5rem;
    line-height: 1.4;
    width: 0.625rem;
    text-align: center;
  }

  .panel-icon.completed { color: hsl(var(--status-success)); }
  .panel-icon.in_progress { color: hsl(var(--status-working)); }
  .panel-icon.pending { color: hsl(var(--muted-foreground)); }

  .panel-content {
    color: hsl(var(--foreground));
    word-break: break-word;
  }

  .panel-item.completed .panel-content {
    text-decoration: line-through;
    color: hsl(var(--muted-foreground));
  }
</style>
