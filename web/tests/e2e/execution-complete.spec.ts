import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, createExecution,
  waitForWorkerIdle, waitForTerminal, waitForTurnEnd,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('complete button visible for input-required execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Complete visible test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  const completeBtn = header.getByRole('button', { name: 'Complete' });
  const cancelBtn = header.getByRole('button', { name: 'Cancel' });
  await expect(completeBtn).toBeVisible({ timeout: 10000 });
  await expect(cancelBtn).toBeVisible();
});

test('complete button opens dialog and completes execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Complete dialog test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  const completeBtn = header.getByRole('button', { name: 'Complete' });
  await expect(completeBtn).toBeVisible({ timeout: 10000 });

  await completeBtn.click();
  await expect(page.getByText('Mark this execution as complete?')).toBeVisible();

  await page.getByRole('button', { name: 'Complete Execution' }).click();

  await expect(page.getByText('Completed', { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(page.locator('.completion-summary')).toContainText('Completed at');
  await expect(completeBtn).not.toBeVisible();
  await expect(header.getByRole('button', { name: 'Cancel' })).not.toBeVisible();
  await expect(header.getByRole('button', { name: 'Re-run' })).toBeVisible();
});

test('complete dialog dismissal keeps execution running', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Complete dismiss test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  const completeBtn = header.getByRole('button', { name: 'Complete' });
  await expect(completeBtn).toBeVisible({ timeout: 10000 });

  await completeBtn.click();
  await expect(page.getByText('Mark this execution as complete?')).toBeVisible();
  await page.getByRole('button', { name: 'Keep Running' }).click();

  await expect(page.getByText('Mark this execution as complete?')).not.toBeVisible();
  await expect(completeBtn).toBeVisible();
});

test('complete button not visible for terminal execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'Complete terminal test');
  await waitForTerminal(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  await expect(header.getByText('Failed', { exact: true })).toBeVisible({ timeout: 10000 });
  await expect(header.getByRole('button', { name: 'Complete' })).not.toBeVisible();
});
