<script lang="ts">
  import type { TodoItem } from '../../types';

  interface Props {
    todos: TodoItem[];
  }

  let { todos }: Props = $props();

  let completedCount = $derived(todos.filter(t => t.status === 'completed').length);

  function statusIcon(status: string): string {
    switch (status) {
      case 'completed': return '\u25CF';   // ●
      case 'in_progress': return '\u25D0'; // ◐
      default: return '\u25CB';            // ○
    }
  }
</script>

<div class="todo-checklist">
  <div class="todo-header">
    <span class="todo-label">Tasks</span>
    <span class="todo-count">{completedCount}/{todos.length} completed</span>
  </div>
  <div class="todo-items">
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

  .todo-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.375rem 0.625rem;
    border-bottom: 1px solid hsl(var(--border) / 0.5);
    font-size: 0.6875rem;
  }

  .todo-label {
    font-weight: 600;
    color: hsl(var(--foreground));
  }

  .todo-count {
    color: hsl(var(--muted-foreground));
    font-family: var(--font-mono);
    font-size: 0.625rem;
  }

  .todo-items {
    padding: 0.25rem 0;
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
