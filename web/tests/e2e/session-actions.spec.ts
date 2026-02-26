import { test, expect } from '@playwright/test';
import {
  apiGet, apiPost, ensureDirectAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd, waitForTerminal,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('session cancel button cancels input-required session', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Session cancel test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // Wait for session tree to render with input-required session
  const sessionNode = page.locator('.tree-node').first();
  await expect(sessionNode).toBeVisible({ timeout: 10000 });

  const cancelBtn = sessionNode.locator('.cancel-btn');
  await expect(cancelBtn).toBeAttached();
  // Hover to make the button visible (opacity transition)
  await sessionNode.hover();
  await cancelBtn.click();

  // Verify session transitions to canceled (scope to session tree to avoid sidebar matches)
  await expect(sessionNode).toContainText('canceled', { timeout: 10000 });
});

test('session complete button completes input-required session', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Session complete test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const sessionNode = page.locator('.tree-node').first();
  await expect(sessionNode).toBeVisible({ timeout: 10000 });

  const completeBtn = sessionNode.locator('.complete-btn');
  await expect(completeBtn).toBeAttached();
  await sessionNode.hover();
  await completeBtn.click();

  // Verify session transitions to completed (scope to session tree to avoid sidebar matches)
  await expect(sessionNode).toContainText('completed', { timeout: 10000 });
});

test('session action buttons hidden on terminal sessions', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'No buttons test');

  // Wait for terminal (failed)
  await waitForTerminal(execId);

  await page.goto(`/#/execution/${execId}`);

  const sessionNode = page.locator('.tree-node').first();
  await expect(sessionNode).toBeVisible({ timeout: 10000 });

  // No cancel or complete buttons on terminal sessions
  await expect(sessionNode.locator('.cancel-btn')).not.toBeAttached();
  await expect(sessionNode.locator('.complete-btn')).not.toBeAttached();
});
