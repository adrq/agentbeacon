import { writable } from 'svelte/store';

export interface Toast {
  id: number;
  message: string;
  type: 'success' | 'error' | 'info';
  timeoutId: ReturnType<typeof setTimeout>;
}

const MAX_TOASTS = 5;
let nextId = 0;

function createToastStore() {
  const { subscribe, update } = writable<Toast[]>([]);

  return {
    subscribe,
    add(message: string, type: Toast['type'] = 'info') {
      const id = nextId++;
      const duration = type === 'error' ? 8000 : 4000;
      const timeoutId = setTimeout(() => {
        update(toasts => toasts.filter(t => t.id !== id));
      }, duration);
      update(toasts => {
        const next = [...toasts, { id, message, type, timeoutId }];
        while (next.length > MAX_TOASTS) {
          const removed = next.shift()!;
          clearTimeout(removed.timeoutId);
        }
        return next;
      });
    },
    dismiss(id: number) {
      update(toasts => {
        const toast = toasts.find(t => t.id === id);
        if (toast) clearTimeout(toast.timeoutId);
        return toasts.filter(t => t.id !== id);
      });
    },
    success(message: string) { this.add(message, 'success'); },
    error(message: string) { this.add(message, 'error'); },
    info(message: string) { this.add(message, 'info'); },
  };
}

export const toasts = createToastStore();

// Expose for E2E tests (production build can't use Vite's dynamic import)
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__toasts = toasts;
}
