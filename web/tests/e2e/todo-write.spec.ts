import { test, expect } from '@playwright/test';
import {
  ensureClaudeAgent, ensureCopilotAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd,
} from './helpers';

test.beforeAll(async () => { await waitForWorkerIdle(); });
test.afterEach(async () => { await waitForWorkerIdle(); });

test('TodoWrite inline checklist renders in Chat view', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo inline test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const checklist = page.locator('.todo-checklist');
  await expect(checklist).toBeVisible({ timeout: 10000 });

  // Header shows counts
  await expect(checklist.locator('.todo-count')).toContainText('3/5 completed');

  // 5 items rendered
  await expect(checklist.locator('.todo-item')).toHaveCount(5);

  // Completed items have strikethrough class
  await expect(checklist.locator('.todo-item.completed')).toHaveCount(3);

  // In-progress item exists
  await expect(checklist.locator('.todo-item.in_progress')).toHaveCount(1);
});

test('TodoPanel sticky above chat input', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo panel test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const panel = page.locator('.todo-panel');
  await expect(panel).toBeVisible({ timeout: 10000 });

  // Counts shown
  await expect(panel.locator('.count-done')).toContainText('3/5 done');
  await expect(panel.locator('.count-working')).toContainText('1 active');

  // Items visible
  await expect(panel.locator('.panel-item')).toHaveCount(5);
});

test('TodoPanel collapses and expands', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo collapse test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const panel = page.locator('.todo-panel');
  const body = panel.locator('.todo-panel-body');
  await expect(body).toBeVisible({ timeout: 10000 });

  // Collapse
  await panel.locator('.todo-panel-header').click();
  await expect(body).not.toBeVisible();

  // Expand
  await panel.locator('.todo-panel-header').click();
  await expect(body).toBeVisible();
});

test('TodoWrite compact entry in Log view', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo log test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);

  // Log view is default — look for the compact Tasks entry
  const todoEntry = page.locator('.timeline-entry').filter({ hasText: 'Tasks (5 items)' });
  await expect(todoEntry).toBeVisible({ timeout: 10000 });
  await expect(todoEntry.locator('.ev-icon')).toContainText('\u2630');
});

test('no TodoPanel when no todos exist', async ({ page }) => {
  const agent = await ensureCopilotAgent();
  const { execId } = await createExecution(agent.id, 'Fix tests', 'No todo test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for chat to render
  await expect(page.locator('.chat-messages')).toBeVisible({ timeout: 10000 });

  // TodoPanel should not exist
  await expect(page.locator('.todo-panel')).not.toBeVisible();
});
