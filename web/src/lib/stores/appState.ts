import { writable } from 'svelte/store';
import type { Screen, Theme } from '../types';

function createThemeStore() {
  const getInitialTheme = (): Theme => {
    if (typeof window === 'undefined') return 'dark';
    const stored = localStorage.getItem('theme');
    return (stored === 'light' || stored === 'dark') ? stored : 'dark';
  };

  const { subscribe, set, update } = writable<Theme>(getInitialTheme());

  const persistAndSet = (value: Theme) => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('theme', value);
    }
    set(value);
  };

  return {
    subscribe,
    set: persistAndSet,
    update: (updater: (value: Theme) => Theme) => {
      update((current) => {
        const next = updater(current);
        if (typeof window !== 'undefined') {
          localStorage.setItem('theme', next);
        }
        return next;
      });
    }
  };
}

export const theme = createThemeStore();
export const currentScreen = writable<Screen>('Home');
export const selectedExecutionId = writable<string | null>(null);
export const selectedProjectId = writable<string | null>(null);
export const selectedFilterProjectId = writable<string | null>(null);
