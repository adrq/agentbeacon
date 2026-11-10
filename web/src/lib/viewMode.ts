// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { EVENT_FILTER_PILLS, type EventFilter } from './eventFilterGroups';

export type ViewMode = 'log' | 'chat' | 'diff';

export const VIEW_OPTIONS: ReadonlyArray<{ value: ViewMode; label: string }> = [
  { value: 'chat', label: 'Chat' },
  { value: 'log',  label: 'Log' },
  { value: 'diff', label: 'Diff' },
];

/** The non-default parts of a view and filter choice, e.g. "Log · Debug"; empty for Chat + All. */
export function viewReminder(mode: ViewMode, filter: EventFilter): string {
  const parts: string[] = [];
  if (mode !== 'chat') parts.push(VIEW_OPTIONS.find(o => o.value === mode)?.label ?? mode);
  if (mode !== 'diff' && filter !== 'all') {
    parts.push(EVENT_FILTER_PILLS.find(p => p.value === filter)?.label ?? filter);
  }
  return parts.join(' · ');
}
