// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { get, writable } from 'svelte/store';
import type { Theme, NavSection, ExecutionPrefill, RouteMode, UsageState, Execution } from '../types';

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
// Execution whose agent-pool dialog is open; set from the sidebar tree or the detail header.
export const agentPoolDialogExecutionId = writable<string | null>(null);
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

// Unread turn-complete tracking: when each execution was last viewed, keyed by
// execution id, as the execution's last_activity_at in epoch milliseconds.
const LAST_VIEWED_KEY = 'agentbeacon-execution-last-viewed';
const UNREAD_BASELINE_KEY = 'agentbeacon-unread-baseline';

export function parseLastViewed(raw: string | null): Record<string, number> {
  try {
    const parsed: unknown = JSON.parse(raw ?? '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const entries = Object.entries(parsed).filter(
      (entry): entry is [string, number] => typeof entry[1] === 'number' && Number.isFinite(entry[1]),
    );
    return Object.fromEntries(entries);
  } catch {
    return {};
  }
}

function readLastViewed(): Record<string, number> {
  return parseLastViewed(safeGetItem(LAST_VIEWED_KEY));
}

// Combines two maps, keeping the later viewed time for each execution.
export function mergeLastViewed(a: Record<string, number>, b: Record<string, number>): Record<string, number> {
  const merged = { ...a };
  for (const [id, viewedAt] of Object.entries(b)) {
    if (viewedAt > (merged[id] ?? 0)) merged[id] = viewedAt;
  }
  return merged;
}

// Activity at or before this time never counts as unread; set on first load.
export const unreadBaseline: number = (() => {
  const stored = Number(safeGetItem(UNREAD_BASELINE_KEY));
  if (Number.isFinite(stored) && stored > 0) return stored;
  const now = Date.now();
  safeSetItem(UNREAD_BASELINE_KEY, String(now));
  return now;
})();

export const executionLastViewed = writable<Record<string, number>>(readLastViewed());

if (typeof window !== 'undefined') {
  window.addEventListener('storage', e => {
    if (e.key !== LAST_VIEWED_KEY) return;
    const stored = parseLastViewed(e.newValue);
    const merged = mergeLastViewed(get(executionLastViewed), stored);
    // Write back entries the other tab's write dropped.
    if (Object.entries(merged).some(([id, viewedAt]) => viewedAt > (stored[id] ?? 0))) {
      safeSetItem(LAST_VIEWED_KEY, JSON.stringify(merged));
    }
    executionLastViewed.set(merged);
  });
}

export function markExecutionViewed(id: string, lastActivityAt: string): void {
  const viewedAt = Date.parse(lastActivityAt);
  if (!Number.isFinite(viewedAt)) return;
  if ((get(executionLastViewed)[id] ?? 0) >= viewedAt) return;
  const next = mergeLastViewed(mergeLastViewed(get(executionLastViewed), readLastViewed()), { [id]: viewedAt });
  safeSetItem(LAST_VIEWED_KEY, JSON.stringify(next));
  executionLastViewed.set(next);
}

export function isExecutionUnread(
  execution: Pick<Execution, 'id' | 'status' | 'last_activity_at'>,
  lastViewed: Record<string, number>,
): boolean {
  if (execution.status !== 'awaiting_input') return false;
  return Date.parse(execution.last_activity_at) > Math.max(unreadBaseline, lastViewed[execution.id] ?? 0);
}
