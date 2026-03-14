import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent,
  ensureDemoAgent,
  createExecution,
  waitForTurnEnd,
  waitForWorkerIdle,
  waitForWorkerPickup,
  apiPost,
} from './helpers';

test.beforeAll(async () => { await waitForWorkerIdle(); });
test.afterEach(async () => { await waitForWorkerIdle(); });

test('ops summary tiles render in 2x2 grid', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', '2x2 grid test');
  await waitForTurnEnd(execId);

  await page.goto('/');
  await expect(page.locator('.ops-summary')).toBeVisible();
  const tiles = page.locator('.ops-summary .tile');
  await expect(tiles).toHaveCount(4);

  // Verify the grid is 2-column by checking tile positions
  const box0 = await tiles.nth(0).boundingBox();
  const box1 = await tiles.nth(1).boundingBox();
  const box2 = await tiles.nth(2).boundingBox();
  expect(box0 && box1 && box2).toBeTruthy();
  // First two tiles on same row, third tile on next row
  expect(box0!.y).toBeCloseTo(box1!.y, 0);
  expect(box2!.y).toBeGreaterThan(box0!.y);
});

test('activity feed shows project label when execution has project', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const project = await apiPost('/api/projects', {
    name: 'FeedLabel Project',
    path: '/tmp',
  });
  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    parts: [{ kind: 'text', text: 'STREAM_CHUNKS' }],
    title: 'Feed project label test',
    project_id: project.id,
  });
  await waitForTurnEnd(exec.execution.id);

  await page.goto('/');
  await expect(page.locator('.feed-item', { hasText: 'Feed project label test' })).toBeVisible();

  const feedItem = page.locator('.feed-item', { hasText: 'Feed project label test' });
  await expect(feedItem.locator('.feed-project')).toBeVisible();
  await expect(feedItem.locator('.feed-project')).toHaveText('FeedLabel Project');
});

test('activity feed omits project label when no project', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'No project label test');
  await waitForTurnEnd(execId);

  await page.goto('/');
  const feedItem = page.locator('.feed-item', { hasText: 'No project label test' });
  await expect(feedItem).toBeVisible();
  await expect(feedItem.locator('.feed-project')).toHaveCount(0);
});

test('decisions panel takes equal width as main content on home', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/');
  await expect(page.locator('.ops-summary, .empty-state').first()).toBeVisible();

  const panel = page.getByRole('complementary', { name: 'Decisions panel' });
  await expect(panel).toBeVisible();

  const panelBox = await panel.boundingBox();
  // SplitPanel is inside shell-body; measure the main content area
  const shellBody = page.locator('.shell-body');
  const shellBox = await shellBody.boundingBox();

  expect(panelBox).toBeTruthy();
  expect(shellBox).toBeTruthy();

  // Panel width should be roughly 40-60% of shell body (50/50 with nav rail)
  const ratio = panelBox!.width / shellBox!.width;
  expect(ratio).toBeGreaterThan(0.38);
  expect(ratio).toBeLessThan(0.62);
});

test('decisions panel stays at 320px on executions page', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/#/executions');
  await expect(page.locator('.ops-summary, .empty-state, .sidebar').first()).toBeVisible();

  const panel = page.getByRole('complementary', { name: 'Decisions panel' });
  await expect(panel).toBeVisible();
  const panelBox = await panel.boundingBox();
  expect(panelBox).toBeTruthy();

  // Should be approximately 320px (expanded) or 40px (collapsed)
  expect(panelBox!.width).toBeLessThanOrEqual(350);
});

test('empty decisions panel shows breathing pulse', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.ops-summary, .empty-state').first()).toBeVisible();

  // With no pending decisions, the pulse dot should appear
  const pulseDot = page.locator('.pulse-dot');
  await expect(pulseDot).toBeVisible();
  await expect(page.getByText('No pending decisions')).toBeVisible();
  await expect(page.getByText('Agents operating autonomously')).toBeVisible();
});

test('compact decision card spacing', async ({ page }) => {
  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Compact card test', 'Compact Q&A');
  await waitForWorkerPickup(execId, 15000);

  await page.goto('/');
  const panel = page.getByRole('complementary', { name: 'Decisions panel' });
  await expect(panel.locator('.decision-card').first()).toBeVisible({ timeout: 20000 });

  // Verify card-header has compact padding (0.5rem = 8px vertical)
  const headerPadding = await panel.locator('.card-header').first().evaluate(
    el => getComputedStyle(el).padding
  );
  // Should contain "8px" (0.5rem) not "12px" (0.75rem)
  expect(headerPadding).toContain('8px');
});
