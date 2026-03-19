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

  // Collapsed by default — summary shows "Todo list updated"
  await expect(checklist.locator('.todo-summary')).toContainText('Todo list updated');

  // Summary counts show completion info
  await expect(checklist.locator('.summary-counts')).toContainText('3/5 done');

  // Items not visible in collapsed state
  await expect(checklist.locator('.todo-items')).not.toBeVisible();

  // Click to expand
  await checklist.locator('.todo-summary').click();
  await expect(checklist.locator('.todo-items')).toBeVisible();

  // 5 items rendered
  await expect(checklist.locator('.todo-item')).toHaveCount(5);

  // Completed items have strikethrough class
  await expect(checklist.locator('.todo-item.completed')).toHaveCount(3);

  // In-progress item exists
  await expect(checklist.locator('.todo-item.in_progress')).toHaveCount(1);
});

test('TodoChecklist click-to-expand and click-to-collapse', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo expand test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const checklist = page.locator('.todo-checklist').first();
  await expect(checklist).toBeVisible({ timeout: 10000 });

  const summary = checklist.locator('.todo-summary');
  const items = checklist.locator('.todo-items');

  // Collapsed by default
  await expect(items).not.toBeVisible();
  await expect(summary).toHaveAttribute('aria-expanded', 'false');

  // Click to expand
  await summary.click();
  await expect(items).toBeVisible();
  await expect(summary).toHaveAttribute('aria-expanded', 'true');

  // Click to collapse
  await summary.click();
  await expect(items).not.toBeVisible();
  await expect(summary).toHaveAttribute('aria-expanded', 'false');
});

test('TodoChecklist keyboard activation', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo keyboard test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const checklist = page.locator('.todo-checklist').first();
  await expect(checklist).toBeVisible({ timeout: 10000 });

  const summary = checklist.locator('.todo-summary');
  const items = checklist.locator('.todo-items');

  // Focus the button and press Enter to expand
  await summary.focus();
  await page.keyboard.press('Enter');
  await expect(items).toBeVisible();
  await expect(summary).toHaveAttribute('aria-expanded', 'true');

  // Press Space to collapse
  await page.keyboard.press('Space');
  await expect(items).not.toBeVisible();
  await expect(summary).toHaveAttribute('aria-expanded', 'false');
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

test('TodoPanel keyboard activation', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo panel keyboard test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const panel = page.locator('.todo-panel');
  const header = panel.locator('.todo-panel-header');
  const body = panel.locator('.todo-panel-body');

  await expect(body).toBeVisible({ timeout: 10000 });
  await expect(header).toHaveAttribute('aria-expanded', 'true');

  // Focus the button and press Enter to collapse
  await header.focus();
  await page.keyboard.press('Enter');
  await expect(body).not.toBeVisible();
  await expect(header).toHaveAttribute('aria-expanded', 'false');

  // Press Space to expand back
  await page.keyboard.press('Space');
  await expect(body).toBeVisible();
  await expect(header).toHaveAttribute('aria-expanded', 'true');
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

test('TodoChecklist aria-controls references mounted body element', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo aria-controls test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const checklist = page.locator('.todo-checklist').first();
  await expect(checklist).toBeVisible({ timeout: 10000 });

  // aria-controls value must match the id on the body element
  const ariaControls = await checklist.locator('.todo-summary').getAttribute('aria-controls');
  expect(ariaControls).toBeTruthy();

  // Body element is in the DOM even when collapsed (not conditionally rendered)
  await expect(page.locator(`#${ariaControls}`)).toBeAttached();
  // But not visible when collapsed
  await expect(page.locator(`#${ariaControls}`)).not.toBeVisible();
});

test('TodoChecklist unique aria-controls IDs across multiple instances', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze codebase', 'Todo unique ids test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const checklists = page.locator('.todo-checklist');
  await expect(checklists.first()).toBeVisible({ timeout: 10000 });

  const count = await checklists.count();

  // Collect all aria-controls values from summary buttons
  const ariaControlsValues: string[] = [];
  for (let i = 0; i < count; i++) {
    const val = await checklists.nth(i).locator('.todo-summary').getAttribute('aria-controls');
    expect(val).toBeTruthy();
    ariaControlsValues.push(val!);
  }

  // Each aria-controls value must look like a UUID-fragment ID (not a static string)
  for (const val of ariaControlsValues) {
    expect(val).toMatch(/^todo-checklist-body-[0-9a-f]{8}$/);
  }

  // If multiple checklists exist, all IDs must be distinct
  if (count > 1) {
    const unique = new Set(ariaControlsValues);
    expect(unique.size).toBe(count);
  }
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
