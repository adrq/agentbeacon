// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

/** The SSE stream URL carries no replay cursor. */
import { test, expect } from '@playwright/test';
import { streamUrl } from '../../src/lib/sseBatch';

test('streamUrl never carries a replay cursor', () => {
  const base = '/api/v1/executions/abc/events/stream';
  expect(streamUrl(base)).toBe(base);
});
