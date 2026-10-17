// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect, type BrowserContext, type Page } from '@playwright/test';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const BASELINE_KEY = 'agentbeacon-unread-baseline';
const VIEWED_KEY = 'agentbeacon-execution-last-viewed';

type Execution = Record<string, unknown> & { id: string; last_activity_at: string };

function isoAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

function makeExecution(overrides: Partial<Execution> & { id: string }): Execution {
  return {
    project_id: null,
    parent_execution_id: null,
    context_id: 'ctx',
    desired: 'run',
    outcome: null,
    status: 'awaiting_input',
    completion_eligible: false,
    title: null,
    metadata: {},
    max_depth: 3,
    max_width: 3,
    sandbox_policy: { fs_level: 'workspace' },
    created_at: isoAgo(2 * HOUR),
    updated_at: isoAgo(2 * HOUR),
    last_activity_at: isoAgo(2 * HOUR),
    completed_at: null,
    ...overrides,
  };
}

async function seedBaseline(target: Page | BrowserContext, baseline: number) {
  await target.addInitScript(({ key, value }) => {
    try {
      if (localStorage.getItem(key) === null) localStorage.setItem(key, value);
    } catch {
    }
  }, { key: BASELINE_KEY, value: String(baseline) });
}

interface Mocks {
  list: () => Execution[];
  detail?: (id: string) => Execution | undefined;
}

async function mockApi(target: Page | BrowserContext, mocks: Mocks) {
  await target.route('**/api/v1/executions*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ json: mocks.list() });
  });
  await target.route('**/api/v1/executions/*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
    const execution = mocks.detail?.(id);
    if (!execution) return route.fulfill({ status: 404, json: { error: 'not found' } });
    return route.fulfill({ json: { execution, sessions: [] } });
  });
  await target.route('**/api/v1/executions/*/sessions', route => route.fulfill({ json: [] }));
  await target.route('**/api/v1/executions/*/agents', route => route.fulfill({ json: [] }));
  await target.route('**/api/v1/decisions*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ json: { decisions: [] } });
  });
}

function row(page: Page, title: string) {
  return page.locator('.exec-list .exec-item', { hasText: title });
}

function dot(scope: ReturnType<Page['locator']>) {
  return scope.getByRole('img', { name: 'Unread' });
}

async function storedViewed(page: Page): Promise<Record<string, number>> {
  const raw = await page.evaluate(key => localStorage.getItem(key), VIEWED_KEY);
  return raw ? JSON.parse(raw) : {};
}

test('a viewed execution stays read after a reload and shows the dot again when it has new activity', async ({ page }) => {
  const a = makeExecution({ id: 'exec-read-a', title: 'Read Exec A', last_activity_at: isoAgo(5 * MINUTE) });
  const b = makeExecution({ id: 'exec-read-b', title: 'Read Exec B', last_activity_at: isoAgo(5 * MINUTE) });
  let listA = a;
  await seedBaseline(page, Date.now() - HOUR);
  await mockApi(page, { list: () => [listA, b], detail: id => (id === a.id ? listA : undefined) });

  await page.goto('/#/executions');
  await expect(dot(row(page, 'Read Exec A'))).toBeVisible();
  await expect(dot(row(page, 'Read Exec B'))).toBeVisible();
  await expect(dot(row(page, 'Read Exec A'))).toHaveAttribute('title', 'Unread');
  await expect(page.getByRole('img', { name: 'Unread' })).toHaveCount(2);

  await row(page, 'Read Exec A').click();
  await expect(dot(row(page, 'Read Exec A'))).toHaveCount(0);
  await expect(dot(row(page, 'Read Exec B'))).toBeVisible();

  const viewed = await storedViewed(page);
  expect(viewed[a.id]).toBe(Date.parse(a.last_activity_at));
  expect(viewed[b.id]).toBeUndefined();

  await page.goto('/#/executions');
  await page.reload();
  await expect(row(page, 'Read Exec A')).toBeVisible();
  await expect(dot(row(page, 'Read Exec B'))).toBeVisible();
  await expect(page.locator('.exec-item[aria-current]')).toHaveCount(0);
  await expect(dot(row(page, 'Read Exec A'))).toHaveCount(0);

  listA = { ...a, last_activity_at: new Date().toISOString() };
  await expect(dot(row(page, 'Read Exec A'))).toBeVisible({ timeout: 15_000 });
});

test('existing executions do not show unread dots on first load', async ({ page }) => {
  await mockApi(page, {
    list: () => [
      makeExecution({ id: 'exec-first-idle', title: 'First Load Idle', last_activity_at: isoAgo(5 * MINUTE) }),
      makeExecution({ id: 'exec-first-working', title: 'First Load Working', status: 'working', last_activity_at: isoAgo(5 * MINUTE) }),
    ],
  });

  const testStart = Date.now();
  await page.goto('/#/executions');
  await expect(row(page, 'First Load Idle')).toBeVisible();
  await expect(row(page, 'First Load Working')).toBeVisible();
  const pageNowAfterLoad = await page.evaluate(() => Date.now());

  await expect(page.getByRole('img', { name: 'Unread' })).toHaveCount(0);
  const baseline = Number(await page.evaluate(key => localStorage.getItem(key), BASELINE_KEY));
  expect(Number.isFinite(baseline)).toBe(true);
  expect(baseline).toBeGreaterThanOrEqual(testStart);
  expect(baseline).toBeLessThanOrEqual(pageNowAfterLoad);
});

test('a turn that completes while its execution is open does not leave an unread dot', async ({ page }) => {
  const a = makeExecution({ id: 'exec-open-a', title: 'Open Exec A', status: 'working', last_activity_at: isoAgo(MINUTE) });
  const d = makeExecution({ id: 'exec-open-d', title: 'Open Exec D', status: 'working', last_activity_at: isoAgo(MINUTE) });
  let listA = a;
  let listD = d;
  await seedBaseline(page, Date.now() - HOUR);
  await mockApi(page, { list: () => [listA, listD], detail: id => (id === a.id ? a : undefined) });

  await page.goto(`/#/execution/${a.id}`);
  await expect(row(page, 'Open Exec A').locator('.exec-status')).toHaveText('working');

  const now = new Date().toISOString();
  listA = { ...a, status: 'awaiting_input', last_activity_at: now };
  listD = { ...d, status: 'awaiting_input', last_activity_at: now };

  await expect(row(page, 'Open Exec A').locator('.exec-status')).toHaveText('turn complete', { timeout: 15_000 });
  await expect(row(page, 'Open Exec D').locator('.exec-status')).toHaveText('turn complete', { timeout: 15_000 });
  await expect(dot(row(page, 'Open Exec D'))).toBeVisible();
  await expect(dot(row(page, 'Open Exec A'))).toHaveCount(0);
});

for (const variant of ['listed', 'pinned'] as const) {
  test(`an execution opened in a hidden tab shows an unread dot until the tab becomes visible (${variant})`, async ({ page }) => {
    const listTime = isoAgo(5 * MINUTE);
    const detailTime = variant === 'listed' ? isoAgo(2 * MINUTE) : listTime;
    const listA = makeExecution({ id: 'exec-hidden-a', title: 'Hidden Exec A', last_activity_at: listTime });
    const detailA = { ...listA, last_activity_at: detailTime };
    const other = makeExecution({ id: 'exec-hidden-other', title: 'Hidden Other', status: 'completed', outcome: 'completed' });

    await seedBaseline(page, Date.now() - HOUR);
    await page.addInitScript(initial => {
      const w = window as unknown as { __visibility?: string };
      w.__visibility = initial;
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => w.__visibility ?? 'visible' });
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => w.__visibility === 'hidden' });
    }, 'hidden');
    await mockApi(page, {
      list: () => (variant === 'listed' ? [listA, other] : [other]),
      detail: id => (id === listA.id ? detailA : undefined),
    });

    const detailResponse = page.waitForResponse(
      resp => new URL(resp.url()).pathname === `/api/v1/executions/${listA.id}`,
    );
    await page.goto(`/#/execution/${listA.id}`);
    await expect(dot(row(page, 'Hidden Exec A'))).toBeVisible();

    await detailResponse;
    await expect(page.getByRole('heading', { level: 2, name: 'Hidden Exec A' })).toBeVisible();
    if (variant === 'listed') {
      await expect(page.locator('.pinned-item')).toHaveCount(0);
      await expect(row(page, 'Hidden Exec A')).toBeVisible();
    } else {
      await expect(page.locator('.pinned-item .exec-item', { hasText: 'Hidden Exec A' })).toBeVisible();
      await expect(row(page, 'Hidden Other')).toBeVisible();
    }

    await page.evaluate(v => {
      (window as unknown as { __visibility?: string }).__visibility = v;
      document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
    }, 'visible');

    await expect(dot(row(page, 'Hidden Exec A'))).toHaveCount(0);
    if (variant === 'listed') {
      await expect.poll(async () => (await storedViewed(page))[listA.id]).toBe(Date.parse(detailTime));
    }
  });
}

test('viewing an execution in one tab clears its unread dot in another tab', async ({ page, context }) => {
  const a = makeExecution({ id: 'exec-tabs-a', title: 'Tabs Exec A', last_activity_at: isoAgo(5 * MINUTE) });
  const b = makeExecution({ id: 'exec-tabs-b', title: 'Tabs Exec B', last_activity_at: isoAgo(5 * MINUTE) });
  await seedBaseline(context, Date.now() - HOUR);
  await mockApi(context, { list: () => [a, b], detail: id => (id === a.id ? a : undefined) });

  await page.goto('/#/executions');
  await expect(dot(row(page, 'Tabs Exec A'))).toBeVisible();
  await expect(dot(row(page, 'Tabs Exec B'))).toBeVisible();

  const other = await context.newPage();
  await other.goto(`/#/execution/${a.id}`);
  await expect(dot(row(other, 'Tabs Exec A'))).toHaveCount(0);

  await expect(dot(row(page, 'Tabs Exec A'))).toHaveCount(0);
  await expect(dot(row(page, 'Tabs Exec B'))).toBeVisible();
  await other.close();
});

test('the unread dot is shown in the sidebar list but not on the agent page', async ({ page }) => {
  const agentId = 'agent-unread';
  const a = makeExecution({
    id: 'exec-agent-a',
    title: 'Agent Page Exec',
    metadata: { agent_id: agentId },
    last_activity_at: isoAgo(5 * MINUTE),
  });
  await seedBaseline(page, Date.now() - HOUR);
  await mockApi(page, { list: () => [a] });
  await page.route(`**/api/v1/agents/${agentId}`, route => route.fulfill({
    json: {
      id: agentId,
      name: 'Unread Agent',
      description: null,
      agent_type: 'claude_sdk',
      driver_id: null,
      enabled: true,
      system_prompt: null,
      config: {},
      sandbox_config: null,
      created_at: isoAgo(HOUR),
      updated_at: isoAgo(HOUR),
    },
  }));
  await page.route('**/api/v1/drivers', route => route.fulfill({ json: [] }));

  await page.goto(`/#/agents/${agentId}`);

  await expect(
    row(page, 'Agent Page Exec').getByRole('img', { name: 'Unread', includeHidden: true }),
  ).toBeAttached();

  const agentPageRow = page.locator('.executions-section .exec-item', { hasText: 'Agent Page Exec' });
  await expect(agentPageRow).toBeVisible();
  await expect(dot(agentPageRow)).toHaveCount(0);
});
