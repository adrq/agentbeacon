// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect } from '@playwright/test';
import {
  isExecutionUnread,
  mergeLastViewed,
  parseLastViewed,
  unreadBaseline,
} from '../../src/lib/stores/appState';

const SECOND = 1000;

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

function execution(status: string, lastActivityMs: number | string) {
  return {
    id: 'exec-1',
    status,
    last_activity_at: typeof lastActivityMs === 'string' ? lastActivityMs : iso(lastActivityMs),
  } as Parameters<typeof isExecutionUnread>[0];
}

test('parseLastViewed returns an empty map for anything but a JSON object', () => {
  for (const raw of [null, '', '{bad', 'null', '[]', '[1, 2]', '42', '"text"', 'true']) {
    expect(parseLastViewed(raw), String(raw)).toEqual({});
  }
});

test('parseLastViewed keeps finite numeric entries and drops the rest', () => {
  const raw = '{"a": 1, "b": "2", "c": null, "d": 1e999, "e": {}, "f": -5}';
  expect(parseLastViewed(raw)).toEqual({ a: 1, f: -5 });
});

test('mergeLastViewed keeps the later time for each execution and entries from both maps', () => {
  const a = { x: 10, y: 50 };
  const b = { y: 40, z: 30 };
  const aCopy = { ...a };
  const bCopy = { ...b };

  expect(mergeLastViewed(a, b)).toEqual({ x: 10, y: 50, z: 30 });
  expect(mergeLastViewed(b, a)).toEqual({ x: 10, y: 50, z: 30 });
  expect(a).toEqual(aCopy);
  expect(b).toEqual(bCopy);
});

test('isExecutionUnread is true only for awaiting_input', () => {
  const activity = unreadBaseline + 60 * SECOND;
  expect(isExecutionUnread(execution('awaiting_input', activity), {})).toBe(true);
  for (const status of ['working', 'completed', 'failed', 'canceled']) {
    expect(isExecutionUnread(execution(status, activity), {}), status).toBe(false);
  }
});

test('isExecutionUnread requires activity strictly after the later of the baseline and the viewed time', () => {
  const b = unreadBaseline;

  expect(isExecutionUnread(execution('awaiting_input', b), {})).toBe(false);
  expect(isExecutionUnread(execution('awaiting_input', b + 1), {})).toBe(true);

  const viewed = { 'exec-1': b + 60 * SECOND };
  expect(isExecutionUnread(execution('awaiting_input', b + 30 * SECOND), viewed)).toBe(false);
  expect(isExecutionUnread(execution('awaiting_input', b + 60 * SECOND), viewed)).toBe(false);
  expect(isExecutionUnread(execution('awaiting_input', b + 60 * SECOND + 1), viewed)).toBe(true);

  const viewedBeforeBaseline = { 'exec-1': b - 60 * SECOND };
  expect(isExecutionUnread(execution('awaiting_input', b - 30 * SECOND), viewedBeforeBaseline)).toBe(false);
  expect(isExecutionUnread(execution('awaiting_input', b + 1), viewedBeforeBaseline)).toBe(true);
});

test('isExecutionUnread is false when last_activity_at is not a valid timestamp', () => {
  expect(isExecutionUnread(execution('awaiting_input', 'not a date'), {})).toBe(false);
});
