// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect, type Page } from '@playwright/test';

// Verifies sidebar execution ordering (ExecutionList.svelte): executions
// needing a response sort first, then running, then idle, then finished —
// regardless of recency. Within a tier, the most recently active execution sorts
// first. Expected top-to-bottom order:
//   pending question  →  working  →  idle awaiting_input  →  completed
//
// Fully network-mocked so it is deterministic and needs no seeded backend data.

const PENDING_EXEC_ID = 'exec-pending-question';
const WORKING_EXEC_ID = 'exec-working';
const IDLE_EXEC_ID = 'exec-idle-awaiting';
const COMPLETED_EXEC_ID = 'exec-completed';

const TITLE_PENDING = 'Pending Question Exec';
const TITLE_WORKING = 'Working Exec';
const TITLE_IDLE = 'Idle Awaiting Exec';
const TITLE_COMPLETED = 'Completed Exec';

function makeExecution(overrides: Record<string, unknown>) {
  return {
    id: 'exec',
    project_id: null,
    parent_execution_id: null,
    context_id: 'ctx',
    desired: 'run',
    outcome: null,
    status: 'working',
    completion_eligible: false,
    title: null,
    metadata: {},
    max_depth: 3,
    max_width: 3,
    sandbox_policy: { fs_level: 'workspace' },
    created_at: '2026-07-24T10:00:00Z',
    updated_at: '2026-07-24T10:00:00Z',
    last_activity_at: '2026-07-24T10:00:00Z',
    completed_at: null,
    ...overrides,
  };
}

const EXECUTIONS = [
  makeExecution({
    id: IDLE_EXEC_ID,
    title: TITLE_IDLE,
    status: 'awaiting_input',
    last_activity_at: '2026-07-24T12:00:00Z',
  }),
  makeExecution({
    id: COMPLETED_EXEC_ID,
    title: TITLE_COMPLETED,
    status: 'completed',
    outcome: 'completed',
    completed_at: '2026-07-24T11:30:00Z',
    last_activity_at: '2026-07-24T11:30:00Z',
  }),
  makeExecution({
    id: WORKING_EXEC_ID,
    title: TITLE_WORKING,
    status: 'working',
    last_activity_at: '2026-07-24T10:30:00Z',
  }),
  makeExecution({
    id: PENDING_EXEC_ID,
    title: TITLE_PENDING,
    status: 'awaiting_input', // idle status; flagged as pending question below
    last_activity_at: '2026-07-24T09:00:00Z',
  }),
];

// One pending decision batch flags PENDING_EXEC_ID as needing a response.
const DECISIONS = {
  decisions: [
    {
      batch_id: 'batch-1',
      execution_id: PENDING_EXEC_ID,
      execution_title: TITLE_PENDING,
      session_id: 'session-1',
      agent_name: 'Test Agent',
      hierarchical_name: 'root',
      status: 'pending',
      importance: 'blocking',
      questions: [
        { question: 'Proceed with the risky migration?', context: null, options: null, batch_index: 0 },
      ],
      answer: null,
      answered_at: null,
      dismissed_at: null,
      created_at: '2026-07-24T09:00:00Z',
    },
  ],
};

async function mockApi(page: Page) {
  // List GET only — the trailing `*` matches optional query params but not the
  // `/api/v1/executions/{id}` detail path (a `*` does not cross `/`).
  await page.route('**/api/v1/executions*', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(EXECUTIONS) });
    } else {
      route.continue();
    }
  });
  await page.route('**/api/v1/decisions*', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(DECISIONS) });
    } else {
      route.continue();
    }
  });
}

test.afterEach(async ({ page }) => {
  await page.unroute('**/api/v1/executions*');
  await page.unroute('**/api/v1/decisions*');
});

test('sidebar sorts pending-question above working above idle above terminal', async ({ page }) => {
  await mockApi(page);

  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  // Attention banner only appears once a pending question is present — waiting
  // on it ensures the question flag is applied before we assert order.
  await expect(page.locator('.attention-banner')).toBeVisible();

  const items = page.locator('.exec-list .exec-item');
  await expect(items).toHaveCount(4);

  const titles = await page.locator('.exec-list .exec-item .exec-title').allTextContents();
  expect(titles).toEqual([TITLE_PENDING, TITLE_WORKING, TITLE_IDLE, TITLE_COMPLETED]);
});

async function mockDecisionsEmpty(page: Page) {
  await page.route('**/api/v1/decisions*', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify({ decisions: [] }) });
    } else {
      route.continue();
    }
  });
}

async function mockExecutionList(page: Page, body: () => unknown[]) {
  await page.route('**/api/v1/executions*', route => {
    if (route.request().method() !== 'GET') {
      route.continue();
      return;
    }
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(body()) });
  });
}

const OLDER_EXEC_ID = 'exec-older-activity';
const NEWER_EXEC_ID = 'exec-newer-activity';
const MIDDLE_EXEC_ID = 'exec-middle-activity';
const TITLE_OLDER = 'Older Activity Exec';
const TITLE_NEWER = 'Newer Activity Exec';
const TITLE_MIDDLE = 'Middle Activity Exec';

test('within a tier, newer last activity sorts first', async ({ page }) => {
  await mockExecutionList(page, () => [
    makeExecution({
      id: OLDER_EXEC_ID,
      title: TITLE_OLDER,
      status: 'awaiting_input',
      updated_at: '2026-07-24T12:00:00Z',
      last_activity_at: '2026-07-24T09:00:00Z',
    }),
    makeExecution({
      id: MIDDLE_EXEC_ID,
      title: TITLE_MIDDLE,
      status: 'awaiting_input',
      updated_at: '2026-07-24T11:00:00Z',
      last_activity_at: '2026-07-24T10:00:00Z',
    }),
    makeExecution({
      id: NEWER_EXEC_ID,
      title: TITLE_NEWER,
      status: 'awaiting_input',
      updated_at: '2026-07-24T08:00:00Z',
      last_activity_at: '2026-07-24T11:00:00Z',
    }),
  ]);
  await mockDecisionsEmpty(page);

  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  await expect(page.locator('.exec-list .exec-item')).toHaveCount(3);
  expect(await page.locator('.exec-list .exec-item .exec-title').allTextContents())
    .toEqual([TITLE_NEWER, TITLE_MIDDLE, TITLE_OLDER]);
});

test('an execution moves up when its last activity advances', async ({ page }) => {
  let olderActivity = '2026-07-24T09:00:00Z';
  await mockExecutionList(page, () => [
    makeExecution({
      id: NEWER_EXEC_ID,
      title: TITLE_NEWER,
      status: 'awaiting_input',
      last_activity_at: '2026-07-24T11:00:00Z',
    }),
    makeExecution({
      id: OLDER_EXEC_ID,
      title: TITLE_OLDER,
      status: 'awaiting_input',
      last_activity_at: olderActivity,
    }),
  ]);
  await mockDecisionsEmpty(page);

  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  const titles = page.locator('.exec-list .exec-item .exec-title');
  await expect(titles).toHaveText([TITLE_NEWER, TITLE_OLDER]);

  olderActivity = '2026-07-24T12:00:00Z';
  await expect(titles).toHaveText([TITLE_OLDER, TITLE_NEWER], { timeout: 15_000 });
});

test('sidebar requests 100 executions', async ({ page }) => {
  await mockExecutionList(page, () => [
    makeExecution({ id: NEWER_EXEC_ID, title: TITLE_NEWER, status: 'awaiting_input' }),
  ]);
  await mockDecisionsEmpty(page);

  const listRequest = page.waitForRequest(req => {
    const url = new URL(req.url());
    return req.method() === 'GET' && url.pathname === '/api/v1/executions';
  });

  await page.goto('/#/executions');
  const url = new URL((await listRequest).url());
  expect(url.searchParams.get('limit')).toBe('100');
  await expect(page.locator('.exec-list .exec-item')).toHaveCount(1);
});

function isoAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

const MINUTE_MS = 60_000;
const DAY_MS = 24 * 60 * MINUTE_MS;

test('home activity feed orders by last activity and shows its relative time', async ({ page }) => {
  const recent = isoAgo(5 * MINUTE_MS + 30_000);
  const old = isoAgo(3 * DAY_MS + 12 * 60 * MINUTE_MS);
  await mockExecutionList(page, () => [
    makeExecution({
      id: OLDER_EXEC_ID,
      title: TITLE_OLDER,
      status: 'awaiting_input',
      updated_at: recent,
      last_activity_at: old,
    }),
    makeExecution({
      id: NEWER_EXEC_ID,
      title: TITLE_NEWER,
      status: 'awaiting_input',
      updated_at: old,
      last_activity_at: recent,
    }),
  ]);
  await mockDecisionsEmpty(page);

  await page.goto('/');
  const feedItems = page.locator('.activity-feed .feed-item');
  await expect(feedItems).toHaveCount(2);
  await expect(feedItems.locator('.feed-exec-name')).toHaveText([TITLE_NEWER, TITLE_OLDER]);
  await expect(feedItems.first().locator('.feed-time')).toHaveText('5m');
  await expect(feedItems.nth(1).locator('.feed-time')).toHaveText('3d');
});

test('sidebar item shows time since last activity', async ({ page }) => {
  const recent = isoAgo(5 * MINUTE_MS + 30_000);
  const old = isoAgo(3 * DAY_MS + 12 * 60 * MINUTE_MS);
  await mockExecutionList(page, () => [
    makeExecution({
      id: COMPLETED_EXEC_ID,
      title: TITLE_COMPLETED,
      status: 'completed',
      outcome: 'completed',
      desired: 'terminate',
      created_at: old,
      completed_at: old,
      updated_at: old,
      last_activity_at: recent,
    }),
  ]);
  await mockDecisionsEmpty(page);

  await page.goto('/#/executions');
  const item = page.locator('.exec-list .exec-item', { hasText: TITLE_COMPLETED });
  await expect(item.locator('.exec-time')).toHaveText('5m ago');
});
