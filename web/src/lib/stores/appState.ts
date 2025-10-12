/**
 * Application State Store
 * Svelte writable stores for global application state
 *
 * This file manages client-side application state using Svelte stores.
 * Theme preference is initialized from localStorage and persisted on change.
 */

import { writable } from 'svelte/store';
import type { Screen, RouteParams, Theme } from '../types';

// ============================================================================
// Theme Store (with localStorage persistence)
// ============================================================================

function createThemeStore() {
  const getInitialTheme = (): Theme => {
    if (typeof window === 'undefined') return 'dark';
    const stored = localStorage.getItem('theme');
    return (stored === 'light' || stored === 'dark') ? stored : 'dark';
  };

  const { subscribe, set, update } = writable<Theme>(getInitialTheme());

  return {
    subscribe,
    set: (value: Theme) => {
      if (typeof window !== 'undefined') {
        localStorage.setItem('theme', value);
      }
      set(value);
    },
    update
  };
}

export const theme = createThemeStore();

// ============================================================================
// Route State Stores (managed by router)
// ============================================================================

export const currentScreen = writable<Screen>('Dashboard');
export const routeParams = writable<RouteParams>({});
