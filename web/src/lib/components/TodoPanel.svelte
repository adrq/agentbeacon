<script lang="ts">
  import type { TodoItem } from '../types';

  interface Props {
    todos: TodoItem[];
  }

  let { todos }: Props = $props();
  let collapsed = $state(false);

  let completedCount = $derived(todos.filter(t => t.status === 'completed').length);
  let inProgressCount = $derived(todos.filter(t => t.status === 'in_progress').length);

  function statusIcon(status: string): string {
    switch (status) {
      case 'completed': return '\u25CF';   // ●
      case 'in_progress': return '\u25D0'; // ◐
      default: return '\u25CB';            // ○
    }
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions a11y_click_events_have_key_events -->
<div class="todo-panel">
  <div class="todo-panel-header" onclick={() => collapsed = !collapsed}>
    <span class="panel-chevron" class:open={!collapsed}>&#x25B8;</span>
    <span class="panel-label">Tasks</span>
    <span class="panel-counts">
      {#if inProgressCount > 0}
        <span class="count-working">{inProgressCount} active</span>
        <span class="count-sep">&middot;</span>
      {/if}
      <span class="count-done">{completedCount}/{todos.length} done</span>
    </span>
  </div>
  {#if !collapsed}
    <div class="todo-panel-body scroll-thin">
      {#each todos as item}
        <div class="panel-item {item.status}">
          <span class="panel-icon {item.status}">{statusIcon(item.status)}</span>
          <span class="panel-content">{item.content}</span>
        </div>
      {/each}
    </div>
  {/if}
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
    padding: 0.3125rem 1rem;
    cursor: pointer;
    font-size: 0.6875rem;
    transition: background 0.1s;
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
    font-weight: 600;
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
    gap: 0.375rem;
    padding: 0.125rem 1rem 0.125rem 1.5rem;
    font-size: 0.6875rem;
    line-height: 1.4;
  }

  .panel-icon {
    flex-shrink: 0;
    font-size: 0.5625rem;
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
