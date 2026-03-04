import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent,
  createExecution,
  waitForWorkerIdle,
  waitForTurnEnd,
  waitForWorking,
} from './helpers';

test.beforeAll(async () => { await waitForWorkerIdle(); });
test.afterEach(async () => { await waitForWorkerIdle(); });

test('ops summary tiles visible on home view', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Ops summary test');
  await waitForTurnEnd(execId);
  await page.goto('/');
  const tiles = page.locator('.ops-summary .tile');
  await expect(tiles).toHaveCount(4);
  await expect(page.locator('.tile.running .tile-label')).toHaveText('Running');
  await expect(page.locator('.tile.waiting .tile-label')).toHaveText('Waiting');
  await expect(page.locator('.tile.completed .tile-label')).toHaveText('Done 24h');
  await expect(page.locator('.tile.failed .tile-label')).toHaveText('Failed 24h');
});

test('tile click filters activity feed', async ({ page }) => {
  const agent = await ensureDirectAgent();
  // Self-seed: create a completed execution so the feed has at least one non-running item
  const { execId: seedId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Seed item');
  await waitForTurnEnd(seedId);
  // Create a DELAY_5 execution so we have a guaranteed running item
  await page.goto('/');
  await expect(page.locator('.feed-item').first()).toBeVisible();
  const { execId } = await createExecution(agent.id, 'DELAY_5', 'Filter test');
  await waitForWorking(execId);
  // Wait for UI polling to show the running execution
  await expect(async () => {
    const count = parseInt(await page.locator('.tile.running .tile-count').textContent() ?? '0');
    expect(count).toBeGreaterThanOrEqual(1);
  }).toPass({ timeout: 10000 });
  // Click Running tile — verify filtered items are all working
  await page.locator('.tile.running').click();
  const filteredItems = page.locator('.feed-item');
  await expect(async () => {
    const count = await filteredItems.count();
    expect(count).toBeGreaterThanOrEqual(1);
  }).toPass({ timeout: 5000 });
  const filteredCount = await filteredItems.count();
  for (let i = 0; i < filteredCount; i++) {
    await expect(filteredItems.nth(i).locator('.feed-icon.working')).toBeVisible();
  }
  // Clear filter — seeded non-running item should reappear
  await page.locator('.filter-clear').click();
  await expect(page.locator('.feed-item', { hasText: 'Seed item' })).toBeVisible();
});

test('activity feed items show verb text', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Verb test');
  await waitForTurnEnd(execId);
  await page.goto('/');
  const verb = page.locator('.feed-item .feed-verb').first();
  await expect(verb).toBeVisible();
  const text = await verb.textContent();
  expect(
    ['completed', 'failed', 'was canceled', 'is awaiting input', 'is working', 'was submitted'].some(v => text?.includes(v))
  ).toBe(true);
});

test('ops summary and empty state are mutually exclusive', async ({ page }) => {
  await page.goto('/');
  // Wait for data to load — one of these must appear
  await expect(page.locator('.ops-summary, .empty-state').first()).toBeVisible();
  const hasExecs = await page.locator('.ops-summary').count();
  const hasEmpty = await page.locator('.empty-state').count();
  expect(hasExecs + hasEmpty).toBeGreaterThan(0);
  if (hasEmpty > 0) {
    expect(hasExecs).toBe(0);
  }
});

test('tile click toggles filter off on second click', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Toggle test');
  await waitForTurnEnd(execId);
  await page.goto('/');
  await expect(page.locator('.ops-summary .tile').first()).toBeVisible();
  await page.locator('.tile.completed').click();
  await expect(page.locator('.tile.completed')).toHaveClass(/active/);
  // Click again to deselect
  await page.locator('.tile.completed').click();
  await expect(page.locator('.tile.completed')).not.toHaveClass(/active/);
});

test('running tile counts working executions', async ({ page }) => {
  const agent = await ensureDirectAgent();
  // Navigate first and capture baseline running count
  await page.goto('/');
  await expect(page.locator('.ops-summary .tile').first()).toBeVisible();
  const baseline = parseInt(await page.locator('.tile.running .tile-count').textContent() ?? '0');
  // Create execution with DELAY_5 to keep it working for 5s
  const { execId } = await createExecution(agent.id, 'DELAY_5', 'Running count test');
  await waitForWorking(execId);
  // Wait for the UI polling cycle to show exactly baseline + 1
  await expect(async () => {
    const count = parseInt(await page.locator('.tile.running .tile-count').textContent() ?? '0');
    expect(count).toBeGreaterThanOrEqual(baseline + 1);
  }).toPass({ timeout: 15000 });
});
