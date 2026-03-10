import { test, expect } from '@playwright/test';
import {
  apiPost, apiDelete, ensureDirectAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd, waitForWorking,
} from './helpers';

const cleanupProjectIds: string[] = [];

test.afterEach(async () => {
  await waitForWorkerIdle();
  for (const id of cleanupProjectIds) {
    try { await apiDelete(`/api/projects/${id}`); } catch { /* best effort */ }
  }
  cleanupProjectIds.length = 0;
});

test('filter execution list by project', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const projectA = await apiPost('/api/projects', { name: 'Filter A', path: '/tmp' });
  const projectB = await apiPost('/api/projects', { name: 'Filter B', path: '/tmp' });
  cleanupProjectIds.push(projectA.id, projectB.id);

  const execA = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'Task for A',
    title: 'Exec A',
    project_id: projectA.id,
  });
  const execB = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'Task for B',
    title: 'Exec B',
    project_id: projectB.id,
  });
  await page.goto('/#/executions');

  const execList = page.locator('.exec-list, .execution-list, main');

  const execAItem = execList.locator('.exec-item', { hasText: 'Exec A' }).first();
  const execBItem = execList.locator('.exec-item', { hasText: 'Exec B' }).first();

  await expect(execAItem).toBeVisible({ timeout: 10000 });
  await expect(execBItem).toBeVisible();

  const filter = page.getByLabel('Filter by project');
  await filter.selectOption(projectA.id);

  await expect(execAItem).toBeVisible({ timeout: 10000 });
  await expect(execBItem).not.toBeVisible({ timeout: 10000 });

  await filter.selectOption('');

  await expect(execAItem).toBeVisible({ timeout: 10000 });
  await expect(execBItem).toBeVisible({ timeout: 10000 });
});

// --- Status filter pills ---

test('status filter pills default to All', async ({ page }) => {
  await page.goto('/#/executions');

  const pillGroup = page.locator('[role="radiogroup"][aria-label="Filter by status"]');
  await expect(pillGroup).toBeVisible({ timeout: 10000 });

  const pills = pillGroup.locator('[role="radio"]');
  await expect(pills).toHaveCount(4);
  await expect(pills.nth(0)).toHaveText('All');
  await expect(pills.nth(1)).toHaveText('Active');
  await expect(pills.nth(2)).toHaveText('Done');
  await expect(pills.nth(3)).toHaveText('Fail');

  await expect(pills.nth(0)).toHaveAttribute('aria-checked', 'true');
});

test('status filter Active shows only non-terminal', async ({ page }) => {
  const agent = await ensureDirectAgent();
  // Create a terminal execution by completing a turn then canceling
  const { execId: termExecId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Terminal exec');
  await waitForTurnEnd(termExecId);
  await apiPost(`/api/executions/${termExecId}/cancel`, {});

  // Create an active execution
  const { execId: activeExecId } = await createExecution(agent.id, 'HANG', 'Active exec');
  await waitForWorking(activeExecId);

  await page.goto('/#/executions');

  const activeButton = page.locator('[role="radio"]', { hasText: 'Active' });
  await expect(activeButton).toBeVisible({ timeout: 10000 });
  await activeButton.click();

  await page.waitForTimeout(500);

  const listItems = page.locator('.exec-item');
  const texts = await listItems.allTextContents();
  const hasActive = texts.some(t => t.includes('Active exec'));
  const hasTerminal = texts.some(t => t.includes('Terminal exec'));
  expect(hasActive).toBe(true);
  expect(hasTerminal).toBe(false);
});

test('status filter composes with text search', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'SearchTarget completed');
  await waitForTurnEnd(execId);
  await apiPost(`/api/executions/${execId}/cancel`, {});

  await page.goto('/#/executions');

  // "canceled" maps to the Fail group, not Done — use Fail pill
  const failButton = page.locator('[role="radio"]', { hasText: 'Fail' });
  await expect(failButton).toBeVisible({ timeout: 10000 });
  await failButton.click();

  const searchInput = page.getByPlaceholder('Search executions...');
  await searchInput.fill('SearchTarget');

  await page.waitForTimeout(500);

  const listItems = page.locator('.exec-item');
  const count = await listItems.count();
  expect(count).toBe(1);
  await expect(listItems.first()).toContainText('SearchTarget');
});
