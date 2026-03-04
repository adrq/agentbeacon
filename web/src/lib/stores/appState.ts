import { writable } from 'svelte/store';
import type { Theme, NavSection } from '../types';

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
export const activeSection = writable<NavSection>('home');
export const sidebarOpen = writable<boolean>(false);
export const selectedExecutionId = writable<string | null>(null);
export const selectedProjectId = writable<string | null>(null);
export const selectedAgentId = writable<string | null>(null);
export const selectedFilterProjectId = writable<string | null>(null);

function createPersistedBoolStore(key: string, defaultValue: boolean) {
  const initial = typeof window !== 'undefined'
    ? (localStorage.getItem(key) === 'true' ? true : localStorage.getItem(key) === 'false' ? false : defaultValue)
    : defaultValue;
  const store = writable<boolean>(initial);
  store.subscribe(value => {
    if (typeof window !== 'undefined') {
      localStorage.setItem(key, String(value));
    }
  });
  return store;
}

export const actionPanelCollapsed = createPersistedBoolStore('agentbeacon-action-panel-collapsed', true);
export const userExplicitlyCollapsed = writable<boolean>(false);
export const notificationsEnabled = createPersistedBoolStore('agentbeacon-notifications-enabled', false);

export type HomeFeedFilter = 'running' | 'waiting' | 'completed' | 'failed' | null;
export const homeFeedFilter = writable<HomeFeedFilter>(null);
