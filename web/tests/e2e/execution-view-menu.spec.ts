// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect, type Page } from '@playwright/test';

const LIVE_ID = 'exec-view-live';
const LIVE_TITLE = 'View Menu Live Exec';
const DONE_ID = 'exec-view-done';
const DONE_TITLE = 'View Menu Done Exec';
const MULTI_ID = 'exec-view-multi';
const MULTI_TITLE = 'View Menu Multi Exec';

function makeExecution(id: string, title: string, done: boolean) {
  const at = new Date(Date.now() - 10 * 60_000).toISOString();
  return {
    id,
    project_id: null,
    parent_execution_id: null,
    context_id: `ctx-${id}`,
    desired: done ? 'terminate' : 'run',
    outcome: done ? 'completed' : null,
    status: done ? 'completed' : 'working',
    completion_eligible: false,
    title,
    metadata: {},
    max_depth: 3,
    max_width: 3,
    sandbox_policy: { fs_level: 'workspace' },
    created_at: at,
    updated_at: at,
    last_activity_at: at,
    completed_at: done ? at : null,
  };
}

function makeSession(executionId: string, done: boolean) {
  const at = new Date(Date.now() - 10 * 60_000).toISOString();
  return {
    id: `sess-${executionId}`,
    execution_id: executionId,
    parent_session_id: null,
    agent_id: 'agent-view',
    agent_session_id: null,
    cwd: '/tmp',
    worktree_path: null,
    base_commit_sha: null,
    desired: done ? 'terminate' : 'run',
    executor_state: done ? 'idle' : 'running',
    outcome: done ? 'completed' : null,
    status: done ? 'completed' : 'working',
    desired_by: null,
    worker_id: null,
    command_type: null,
    parent_notified: false,
    recovery_attempts: 0,
    metadata: {},
    sandbox_policy: { fs_level: 'workspace' },
    created_at: at,
    updated_at: at,
    completed_at: done ? at : null,
  };
}

const EXECUTIONS = [
  makeExecution(LIVE_ID, LIVE_TITLE, false),
  makeExecution(DONE_ID, DONE_TITLE, true),
  makeExecution(MULTI_ID, MULTI_TITLE, true),
];

function sessionsFor(id: string, done: boolean) {
  const root = makeSession(id, done);
  if (id !== MULTI_ID) return [root];
  return [root, { ...root, id: `sess-${id}-child`, parent_session_id: root.id }];
}

type StreamMode = 'abort' | 'protocol-error';

async function mockApi(page: Page, stream: StreamMode = 'abort', decisions: unknown[] = []) {
  const counts = { streams: 0 };
  await page.route('**/api/v1/executions*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ json: EXECUTIONS });
  });
  await page.route('**/api/v1/executions/*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
    const execution = EXECUTIONS.find(e => e.id === id);
    if (!execution) return route.fulfill({ status: 404, json: { error: 'not found' } });
    return route.fulfill({ json: { execution, sessions: sessionsFor(id, execution.outcome != null) } });
  });
  await page.route('**/api/v1/executions/*/sessions', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/executions/*/agents', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/sessions/*/events*', route =>
    route.fulfill({ json: { items: [], next_cursor: null, has_more: false } }));
  await page.route('**/api/v1/executions/*/events/stream*', route => {
    counts.streams++;
    if (stream === 'abort') return route.abort();
    return route.fulfill({
      status: 200,
      headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' },
      body:
        'event: position\ndata: {"position":null,"history_before":null}\n\n' +
        'event: protocol_error\ndata: {"code":"internal.error","status":500,' +
        '"title":"Internal error","detail":"the event stream could not be read"}\n\n',
    });
  });
  await page.route('**/api/v1/decisions*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ json: { decisions } });
  });
  return counts;
}

function viewButton(page: Page) {
  return page.locator('.detail-header').getByRole('button', { name: 'View', exact: true });
}

function viewMenu(page: Page) {
  return page.getByRole('menu', { name: 'View' });
}

function reminder(page: Page) {
  return page.locator('.detail-header .view-reminder');
}

async function pick(page: Page, label: string) {
  const item = viewMenu(page).getByRole('menuitemradio', { name: label, exact: true });
  await item.click();
  await expect(item).toHaveAttribute('aria-checked', 'true');
}

test.describe('execution view controls on desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('the View menu switches views, offers filters for Chat and Log only, and closes on Escape', async ({ page }) => {
    await mockApi(page);
    await page.goto(`/#/execution/${DONE_ID}`);
    await expect(page.getByRole('heading', { name: DONE_TITLE })).toBeVisible();

    const button = viewButton(page);
    await expect(button).toHaveAttribute('aria-haspopup', 'menu');
    await expect(button).toHaveAttribute('aria-expanded', 'false');
    await button.click();
    await expect(button).toHaveAttribute('aria-expanded', 'true');
    const menu = viewMenu(page);
    await expect(menu.getByRole('group', { name: 'View mode' }).getByRole('menuitemradio')).toHaveText(['Chat', 'Log', 'Diff']);
    await expect(menu.getByRole('menuitemradio', { name: 'Chat', exact: true })).toBeFocused();
    const filters = menu.getByRole('group', { name: 'Filter' });
    await expect(filters.getByRole('menuitemradio')).toHaveText(['All', 'Messages', 'Tools', 'Errors', 'Status', 'Debug']);

    await pick(page, 'Log');
    await expect(page).toHaveURL(new RegExp(`#/execution/${DONE_ID}$`));
    await expect(filters).toBeVisible();

    await pick(page, 'Diff');
    await expect(page).toHaveURL(/[?&]view=diff/);
    await expect(filters).toHaveCount(0);

    await pick(page, 'Chat');
    await expect(page).toHaveURL(/[?&]view=chat/);
    await expect(filters).toBeVisible();

    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await expect(button).toHaveAttribute('aria-expanded', 'false');
    await expect(button).toBeFocused();
  });

  test('a non-default view shows a reminder chip that resets to Chat and All', async ({ page }) => {
    await mockApi(page);
    await page.goto(`/#/execution/${DONE_ID}`);
    await expect(page.getByRole('heading', { name: DONE_TITLE })).toBeVisible();
    await expect(reminder(page)).toHaveCount(0);

    await viewButton(page).click();
    await pick(page, 'Log');
    await pick(page, 'Debug');
    await page.keyboard.press('Escape');
    await expect(reminder(page)).toHaveText('Log · Debug');

    await reminder(page).getByRole('button', { name: 'Reset view' }).click();
    await expect(reminder(page)).toHaveCount(0);
    await expect(page).toHaveURL(/[?&]view=chat/);
    await viewButton(page).click();
    await expect(viewMenu(page).getByRole('menuitemradio', { name: 'Chat', exact: true })).toHaveAttribute('aria-checked', 'true');
    await expect(viewMenu(page).getByRole('menuitemradio', { name: 'All', exact: true })).toHaveAttribute('aria-checked', 'true');
  });

  test('a stream that fails for good shows Disconnected with a working Retry', async ({ page }) => {
    const counts = await mockApi(page, 'protocol-error');
    await page.goto(`/#/execution/${LIVE_ID}`);
    await expect(page.getByRole('heading', { name: LIVE_TITLE })).toBeVisible();
    await expect.poll(() => counts.streams).toBeGreaterThanOrEqual(1);

    const indicator = page.locator('.detail-header .sse-indicator');
    await expect(indicator).toHaveClass(/(^|\s)disconnected(\s|$)/);
    await expect(indicator.locator('.sse-label')).toHaveText('Disconnected');

    const before = counts.streams;
    await indicator.getByRole('button', { name: 'Retry' }).click();
    await expect.poll(() => counts.streams).toBeGreaterThan(before);
  });

  test('with the decisions panel open the View options move into the overflow menu', async ({ page }) => {
    const decision = {
      batch_id: 'batch-view',
      execution_id: DONE_ID,
      execution_title: DONE_TITLE,
      session_id: `sess-${DONE_ID}`,
      agent_name: 'View Agent',
      hierarchical_name: 'root',
      status: 'pending',
      importance: 'blocking',
      questions: [{ question: 'Keep the narrow layout?', context: null, options: null, batch_index: 0 }],
      answer: null,
      answered_at: null,
      dismissed_at: null,
      created_at: new Date(Date.now() - 60_000).toISOString(),
    };
    await mockApi(page, 'abort', [decision]);
    await page.goto(`/#/execution/${DONE_ID}`);
    await expect(page.getByRole('heading', { name: DONE_TITLE })).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Decisions panel' }).locator('.decision-card')).toBeVisible();

    await expect(viewButton(page)).toBeHidden();
    const more = page.locator('.detail-header').getByRole('button', { name: 'More actions' });
    await expect(more).toBeVisible();
    await expect(more).toHaveAttribute('aria-haspopup', 'menu');
    await expect(more).toHaveAttribute('aria-expanded', 'false');
    await more.click();
    await expect(more).toHaveAttribute('aria-expanded', 'true');
    const overflow = page.getByRole('menu', { name: 'More actions' });
    const agents = overflow.getByRole('menuitem', { name: /^Agents/ });
    await expect(agents).toBeFocused();
    await expect(overflow.getByRole('group', { name: 'View' }).getByRole('menuitemradio')).toHaveText(['Chat', 'Log', 'Diff']);
    await expect(overflow.getByRole('group', { name: 'Filter' }).getByRole('menuitemradio', { name: 'Debug', exact: true })).toBeVisible();

    await page.keyboard.press('ArrowDown');
    await expect(overflow.getByRole('menuitem', { name: 'Re-run', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(overflow).toHaveCount(0);
    await expect(more).toBeFocused();
    await expect(more).toHaveAttribute('aria-expanded', 'false');

    await more.click();
    await overflow.getByRole('menuitemradio', { name: 'Log', exact: true }).click();
    await expect(overflow).toHaveCount(0);
    await expect(more).toBeFocused();
    await expect(reminder(page)).toHaveText('Log');
  });

  test('while the overview is open, the View options and reminder chip are hidden', async ({ page }) => {
    await mockApi(page);
    await page.goto(`/#/execution/${MULTI_ID}`);
    await expect(page.getByRole('heading', { name: MULTI_TITLE })).toBeVisible();

    await viewButton(page).click();
    await pick(page, 'Log');
    await page.keyboard.press('Escape');
    await expect(reminder(page)).toHaveText('Log');

    const overview = page.locator('.detail-header').getByRole('button', { name: 'Overview' });
    await overview.click();
    await expect(overview).toHaveAttribute('aria-pressed', 'true');
    await expect(viewButton(page)).toHaveCount(0);
    await expect(reminder(page)).toHaveCount(0);

    await overview.click();
    await expect(overview).toHaveAttribute('aria-pressed', 'false');
    await expect(viewButton(page)).toBeVisible();
    await expect(reminder(page)).toHaveText('Log');
  });

  test('an open View menu closes when a resize moves View out of the header', async ({ page }) => {
    await mockApi(page);
    await page.goto(`/#/execution/${DONE_ID}`);
    await expect(page.getByRole('heading', { name: DONE_TITLE })).toBeVisible();

    await viewButton(page).click();
    await expect(viewMenu(page)).toBeVisible();

    await page.setViewportSize({ width: 900, height: 800 });
    await expect(viewButton(page)).toBeHidden();
    await expect(viewMenu(page)).toHaveCount(0);

    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(viewButton(page)).toBeVisible();
    await expect(viewButton(page)).toHaveAttribute('aria-expanded', 'false');
    await expect(viewMenu(page)).toHaveCount(0);
  });

  test('a finished execution shows no connection chip', async ({ page }) => {
    await mockApi(page, 'abort');
    await page.goto(`/#/execution/${DONE_ID}`);
    await expect(page.getByRole('heading', { name: DONE_TITLE })).toBeVisible();
    await expect(page.locator('.detail-header')).toContainText('Completed');
    await expect(page.locator('.sse-indicator')).toHaveCount(0);
  });
});

test.describe('mobile More menu', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('opens above the nav, toggles the theme in place, navigates to Settings and closes on Escape', async ({ page }) => {
    await mockApi(page);
    await page.goto('/#/executions');
    await expect(page.locator('.exec-list .exec-item', { hasText: LIVE_TITLE })).toBeVisible();

    const more = page.locator('.nav-rail').getByRole('button', { name: 'More', exact: true });
    const menu = page.locator('#nav-more-menu');
    await more.click();
    await expect(more).toHaveAttribute('aria-expanded', 'true');
    await expect(menu).toBeVisible();
    const menuBox = await menu.boundingBox();
    const navBox = await page.locator('.nav-rail').boundingBox();
    expect(menuBox!.y + menuBox!.height).toBeLessThanOrEqual(navBox!.y);

    const themeButton = menu.getByRole('button', { name: /Activate (light|dark) theme/ });
    const startLight = await page.evaluate(() => document.documentElement.classList.contains('light'));
    await expect(themeButton).toHaveText(startLight ? 'Light' : 'Dark');
    await expect(themeButton).toHaveAttribute('aria-pressed', String(startLight));

    await themeButton.click();
    await expect(themeButton).toHaveText(startLight ? 'Dark' : 'Light');
    await expect(themeButton).toHaveAttribute('aria-pressed', String(!startLight));
    await expect(themeButton).toHaveAccessibleName(startLight ? 'Activate light theme' : 'Activate dark theme');
    expect(await page.evaluate(() => document.documentElement.classList.contains('light'))).toBe(!startLight);
    await expect(menu).toBeVisible();

    await menu.getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page).toHaveURL(/#\/settings$/);
    await expect(menu).toHaveCount(0);
    await expect(more).toHaveClass(/(^|\s)active(\s|$)/);

    await more.click();
    await expect(menu).toBeVisible();
    await page.keyboard.press('Tab');
    await expect(menu.getByRole('button', { name: 'Settings', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await expect(more).toHaveAttribute('aria-expanded', 'false');
    await expect(more).toBeFocused();

    await more.click();
    await expect(menu).toBeVisible();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await expect(menu.getByRole('link', { name: /Docs/ })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(menu).toHaveCount(0);
    await expect(more).toHaveAttribute('aria-expanded', 'false');
  });
});
