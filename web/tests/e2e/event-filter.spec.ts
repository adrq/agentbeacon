import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent,
  createExecution,
  waitForWorkerIdle, waitForTurnEnd,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('filter pills visible in log view with correct labels', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter log test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  // Default is Log view
  const filterGroup = page.getByRole('radiogroup', { name: 'Filter events' });
  await expect(filterGroup).toBeVisible({ timeout: 10000 });

  const pills = filterGroup.getByRole('radio');
  await expect(pills).toHaveCount(5);
  await expect(pills.nth(0)).toHaveText('All');
  await expect(pills.nth(1)).toHaveText('Messages');
  await expect(pills.nth(2)).toHaveText('Tools');
  await expect(pills.nth(3)).toHaveText('Errors');
  await expect(pills.nth(4)).toHaveText('Status');

  // "All" is checked by default
  await expect(pills.nth(0)).toHaveAttribute('aria-checked', 'true');
});

test('filter pills visible in chat view with correct labels', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter chat test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const filterGroup = page.getByRole('radiogroup', { name: 'Filter events' });
  await expect(filterGroup).toBeVisible({ timeout: 10000 });

  const pills = filterGroup.getByRole('radio');
  await expect(pills).toHaveCount(5);
  await expect(pills.nth(0)).toHaveAttribute('aria-checked', 'true');
});

test('"Messages" filter shows only user/agent messages, hides tool calls', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter msg test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Verify tool group visible in "All" mode
  await expect(page.locator('.tool-group').first()).toBeVisible({ timeout: 10000 });

  // Switch to Messages filter
  await page.getByRole('radio', { name: 'Messages' }).click();

  // Tool group should be hidden
  await expect(page.locator('.tool-group')).not.toBeVisible();
  // State rows should be hidden
  await expect(page.locator('.state-row')).not.toBeVisible();
  // User message should still be visible
  await expect(page.locator('.user-bubble')).toBeVisible();
});

test('"Tools" filter shows only tool calls, hides messages', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter tool test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();
  await expect(page.locator('.tool-group').first()).toBeVisible({ timeout: 10000 });

  await page.getByRole('radio', { name: 'Tools' }).click();

  // Tool group should be visible
  await expect(page.locator('.tool-group').first()).toBeVisible();
  // User message should be hidden
  await expect(page.locator('.user-bubble')).not.toBeVisible();
  // State rows should be hidden
  await expect(page.locator('.state-row')).not.toBeVisible();
});

test('"Errors" filter shows only error entries', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'Filter error test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for error panel to be visible in All mode
  await expect(page.locator('.error-panel').first()).toBeVisible({ timeout: 10000 });

  await page.getByRole('radio', { name: 'Errors' }).click();

  // Error panel should be visible
  await expect(page.locator('.error-panel').first()).toBeVisible();
  // User message should be hidden
  await expect(page.locator('.user-bubble')).not.toBeVisible();
});

test('"All" filter shows everything (default)', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter all test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // All radio should be checked
  const filterGroup = page.getByRole('radiogroup', { name: 'Filter events' });
  await expect(filterGroup.getByRole('radio', { name: 'All' })).toHaveAttribute('aria-checked', 'true');

  // Both user messages and tool groups should be visible
  await expect(page.locator('.user-bubble').first()).toBeVisible({ timeout: 10000 });
  await expect(page.locator('.tool-group').first()).toBeVisible();
  await expect(page.locator('.state-row').first()).toBeVisible();
});

test('filter persists when switching between chat and log views', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter persist test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // Set filter to "Messages" in log view
  await page.getByRole('radio', { name: 'Messages' }).click();
  await expect(page.getByRole('radio', { name: 'Messages' })).toHaveAttribute('aria-checked', 'true');

  // Switch to Chat view
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Filter should still be "Messages"
  await expect(page.getByRole('radio', { name: 'Messages' })).toHaveAttribute('aria-checked', 'true');

  // Switch back to Log
  await page.getByRole('tab', { name: 'Log' }).click();
  await expect(page.getByRole('radio', { name: 'Messages' })).toHaveAttribute('aria-checked', 'true');
});

test('filter resets when navigating to different execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId: execId1 } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter reset A');
  const { execId: execId2 } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Filter reset B');
  await waitForTurnEnd(execId1);
  await waitForTurnEnd(execId2);

  // Navigate to first execution and set filter
  await page.goto(`/#/execution/${execId1}`);
  await expect(page.getByRole('radiogroup', { name: 'Filter events' })).toBeVisible({ timeout: 10000 });
  await page.getByRole('radio', { name: 'Tools' }).click();
  await expect(page.getByRole('radio', { name: 'Tools' })).toHaveAttribute('aria-checked', 'true');

  // Navigate to second execution
  await page.goto(`/#/execution/${execId2}`);
  await expect(page.getByRole('radiogroup', { name: 'Filter events' })).toBeVisible({ timeout: 10000 });

  // Filter should reset to "All"
  const filterGroup = page.getByRole('radiogroup', { name: 'Filter events' });
  await expect(filterGroup.getByRole('radio', { name: 'All' })).toHaveAttribute('aria-checked', 'true');
});
