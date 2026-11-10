// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect, type Page } from '@playwright/test';

const EXEC_ID = 'exec-layout';
const EXEC_TITLE = 'Layout Exec';
const OTHER_EXEC_ID = 'exec-layout-other';
const OTHER_EXEC_TITLE = 'Layout Other Exec';

function makeExecution(id: string, title: string) {
  const at = new Date(Date.now() - 10 * 60_000).toISOString();
  return {
    id,
    project_id: null,
    parent_execution_id: null,
    context_id: `ctx-${id}`,
    desired: 'run',
    outcome: null,
    status: 'awaiting_input',
    completion_eligible: false,
    title,
    metadata: {},
    max_depth: 3,
    max_width: 3,
    sandbox_policy: { fs_level: 'workspace' },
    created_at: at,
    updated_at: at,
    last_activity_at: at,
    completed_at: null,
  };
}

const EXECUTIONS = [makeExecution(EXEC_ID, EXEC_TITLE), makeExecution(OTHER_EXEC_ID, OTHER_EXEC_TITLE)];

const DECISIONS = {
  decisions: [
    {
      batch_id: 'batch-layout',
      execution_id: EXEC_ID,
      execution_title: EXEC_TITLE,
      session_id: 'session-layout',
      agent_name: 'Layout Agent',
      hierarchical_name: 'root',
      status: 'pending',
      importance: 'blocking',
      questions: [
        { question: 'Which layout should we keep?', context: null, options: null, batch_index: 0 },
      ],
      answer: null,
      answered_at: null,
      dismissed_at: null,
      created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    },
  ],
};

async function mockApi(page: Page, decisions: unknown = DECISIONS) {
  await page.route('**/api/v1/executions*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ json: EXECUTIONS });
  });
  await page.route('**/api/v1/executions/*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() ?? '');
    const execution = EXECUTIONS.find(e => e.id === id);
    if (!execution) return route.fulfill({ status: 404, json: { error: 'not found' } });
    return route.fulfill({ json: { execution, sessions: [] } });
  });
  await page.route('**/api/v1/executions/*/sessions', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/executions/*/agents', route => route.fulfill({ json: [] }));
  await page.route('**/api/v1/decisions*', route => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ json: decisions });
  });
}

function decisionsPanel(page: Page) {
  return page.locator('aside.action-panel[aria-label="Decisions panel"]');
}

function backdrop(page: Page) {
  return page.getByRole('button', { name: 'Close decisions panel' });
}

function navButton(page: Page, name: string) {
  return page.locator('.nav-rail').getByRole('button', { name, exact: true });
}

async function width(page: Page, selector: string): Promise<number> {
  const box = await page.locator(selector).boundingBox();
  return box?.width ?? -1;
}

test.describe('decisions panel below desktop width', () => {
  test.use({ viewport: { width: 900, height: 800 } });

  test('opens as an overlay from the nav rail and closes from the backdrop or a decision card', async ({ page }) => {
    await page.addInitScript(() => {
      try {
        localStorage.setItem('agentbeacon-action-panel-collapsed', 'false');
      } catch {
      }
    });
    await mockApi(page);

    await page.goto('/#/executions');
    const panel = decisionsPanel(page);
    await expect(page.locator('.exec-list .exec-item', { hasText: EXEC_TITLE })).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Expand decisions panel' })).toBeVisible();
    await expect(panel).toHaveClass(/(^|\s)collapsed(\s|$)/);
    await expect.poll(() => width(page, '.action-panel')).toBe(40);

    const mainWidth = await width(page, '.main-content');

    await navButton(page, 'Toggle decisions panel').click();
    await expect(panel).toHaveClass(/(^|\s)overlay(\s|$)/);
    await expect(panel).not.toHaveClass(/(^|\s)collapsed(\s|$)/);
    const card = panel.locator('.decision-card', { hasText: EXEC_TITLE });
    await expect(card).toBeVisible();
    expect(await width(page, '.main-content')).toBe(mainWidth);

    await backdrop(page).click({ position: { x: 20, y: 400 } });
    await expect(panel).toHaveClass(/(^|\s)collapsed(\s|$)/);
    await expect(backdrop(page)).toHaveCount(0);

    await navButton(page, 'Toggle decisions panel').click();
    await expect(card).toBeVisible();
    await card.getByRole('link', { name: /view execution/ }).click();

    await expect(page).toHaveURL(new RegExp(`#/execution/${EXEC_ID}(\\?|$)`));
    await expect(page.locator('.detail-title')).toHaveText(EXEC_TITLE);
    await expect(panel).toHaveClass(/(^|\s)collapsed(\s|$)/);
    await expect(backdrop(page)).toHaveCount(0);
  });
});

test.describe('mobile navigation', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('the Executions nav item returns from an execution to the list', async ({ page }) => {
    await mockApi(page, { decisions: [] });

    await page.goto(`/#/execution/${EXEC_ID}`);
    await expect(page.locator('.detail-title')).toHaveText(EXEC_TITLE);

    await navButton(page, 'Executions').click();

    await expect(page).toHaveURL(/#\/executions$/);
    await expect(page.locator('.exec-list .exec-item', { hasText: EXEC_TITLE })).toBeVisible();
    await expect(page.locator('.detail-header')).toHaveCount(0);
  });

  test('every nav item closes the decisions overlay, including the current section', async ({ page }) => {
    await mockApi(page);

    await page.goto('/#/executions');
    await expect(page.locator('.exec-list .exec-item', { hasText: EXEC_TITLE })).toBeVisible();
    const panel = decisionsPanel(page);

    const steps: { item: string; url: RegExp }[] = [
      { item: 'Executions', url: /#\/executions$/ },
      { item: 'Projects', url: /#\/projects$/ },
      { item: 'More', url: /#\/projects$/ },
    ];
    for (const { item, url } of steps) {
      await navButton(page, 'Toggle decisions panel').click();
      await expect(panel).not.toHaveClass(/(^|\s)collapsed(\s|$)/);
      await expect(backdrop(page)).toBeVisible();

      await navButton(page, item).click();

      await expect(page, item).toHaveURL(url);
      await expect(panel, item).toHaveClass(/(^|\s)collapsed(\s|$)/);
      await expect(backdrop(page), item).toHaveCount(0);
    }

    await page.locator('#nav-more-menu').getByRole('button', { name: 'Settings', exact: true }).click();
    await expect(page).toHaveURL(/#\/settings$/);
    await expect(page.locator('#nav-more-menu')).toHaveCount(0);
  });
});

test.describe('brand lockup on desktop', () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test('the wordmark stays in place when the sidebar collapses and the header reserves its space', async ({ page }) => {
    await mockApi(page, { decisions: [] });

    await page.goto(`/#/execution/${EXEC_ID}`);
    const wordmark = page.getByRole('link', { name: 'AgentBeacon home' }).locator('.wordmark');
    const title = page.locator('.detail-header .detail-title');
    const crumb = page.locator('.detail-header .crumb-sep');
    await expect(title).toHaveText(EXEC_TITLE);
    await expect(page.locator('.left-panel')).not.toHaveClass(/(^|\s)collapsed(\s|$)/);
    await expect(wordmark).toBeVisible();
    await expect(crumb).toHaveCount(0);
    const open = await wordmark.boundingBox();
    expect(open).not.toBeNull();

    await navButton(page, 'Executions').click();
    await expect(page.locator('.left-panel')).toHaveClass(/(^|\s)collapsed(\s|$)/);
    await expect.poll(() => width(page, '.left-panel')).toBe(0);

    const collapsed = await wordmark.boundingBox();
    expect(collapsed).not.toBeNull();
    expect(collapsed!.x).toBe(open!.x);
    expect(collapsed!.y).toBe(open!.y);

    await expect(crumb).toBeVisible();
    const titleBox = await title.boundingBox();
    expect(titleBox).not.toBeNull();
    expect(titleBox!.x).toBeGreaterThanOrEqual(collapsed!.x + collapsed!.width);
  });
});
