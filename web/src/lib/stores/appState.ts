import { writable } from 'svelte/store';
import type { Theme, NavSection, ExecutionPrefill, RouteMode, UsageState } from '../types';

export function safeGetItem(key: string): string | null {
  try { return typeof window !== 'undefined' ? localStorage.getItem(key) : null; } catch { return null; }
}

export function safeSetItem(key: string, value: string): void {
  try { if (typeof window !== 'undefined') localStorage.setItem(key, value); } catch { /* localStorage unavailable */ }
}

function createThemeStore() {
  const getInitialTheme = (): Theme => {
    const stored = safeGetItem('theme');
    return (stored === 'light' || stored === 'dark') ? stored : 'dark';
  };

  const { subscribe, set, update } = writable<Theme>(getInitialTheme());

  const persistAndSet = (value: Theme) => {
    safeSetItem('theme', value);
    set(value);
  };

  return {
    subscribe,
    set: persistAndSet,
    update: (updater: (value: Theme) => Theme) => {
      update((current) => {
        const next = updater(current);
        safeSetItem('theme', next);
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
export const routeMode = writable<RouteMode>('view');
export const executionPrefill = writable<ExecutionPrefill | null>(null);
export const agentFormPrefill = writable<{ driverId?: string } | null>(null);

function createPersistedBoolStore(key: string, defaultValue: boolean) {
  const stored = safeGetItem(key);
  const initial = stored === 'true' ? true : stored === 'false' ? false : defaultValue;
  const store = writable<boolean>(initial);
  store.subscribe(value => { safeSetItem(key, String(value)); });
  return store;
}

export const actionPanelCollapsed = createPersistedBoolStore('agentbeacon-action-panel-collapsed', true);
export const userExplicitlyCollapsed = writable<boolean>(false);

// Migrate old notification key BEFORE creating stores, so the store picks up the migrated value.
// Only preserve affirmative opt-in (old key was set to true when browser permission was granted).
// Old false means "never interacted", not "explicitly disabled" — let the new default (true) apply.
(() => {
  const oldKey = 'agentbeacon-notifications-enabled';
  const newKey = 'agentbeacon-escalation-notifications-enabled';
  const oldVal = safeGetItem(oldKey);
  if (oldVal !== null) {
    if (oldVal === 'true' && safeGetItem(newKey) === null) {
      safeSetItem(newKey, 'true');
    }
    try { localStorage.removeItem(oldKey); } catch { /* ignore */ }
  }
})();

// Notification preference stores (split from old single `notificationsEnabled`)
export const escalationNotificationsEnabled = createPersistedBoolStore('agentbeacon-escalation-notifications-enabled', true);
export const turnCompleteNotificationsEnabled = createPersistedBoolStore('agentbeacon-turn-complete-notifications-enabled', true);

export type HomeFeedFilter = 'running' | 'waiting' | 'completed' | 'failed' | null;
export const homeFeedFilter = writable<HomeFeedFilter>(null);

export const selectedSessionId = writable<string | null>(null);
export const usageBySession = writable<Map<string, UsageState>>(new Map());
