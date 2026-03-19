import type { TodoItem } from '../types';

/** Status → icon character mapping used by both inline and pinned renderers. */
export function statusIcon(status: string): string {
  switch (status) {
    case 'completed': return '\u25CF';   // ●
    case 'in_progress': return '\u25D0'; // ◐
    default: return '\u25CB';            // ○
  }
}

/** Count helpers for todo summary display. */
export function todoCounts(todos: TodoItem[]) {
  const completed = todos.filter(t => t.status === 'completed').length;
  const inProgress = todos.filter(t => t.status === 'in_progress').length;
  const total = todos.length;
  return { completed, inProgress, total };
}
