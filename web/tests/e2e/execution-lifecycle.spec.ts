import { test, expect } from '@playwright/test';
import {
  apiGet, ensureDirectAgent, createExecution,
  waitForWorkerIdle, waitForTerminal, waitForTurnEnd, waitForWorking,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Cancel execution while agent is actively working ---

test('cancel execution: dialog confirm cancels running execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'HANG', 'Cancel test');
  await waitForWorking(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  const cancelBtn = header.getByRole('button', { name: 'Cancel' });
  await expect(cancelBtn).toBeVisible({ timeout: 10000 });

  await cancelBtn.click();
  await expect(page.getByText('Cancel this execution?')).toBeVisible();

  await page.getByRole('button', { name: 'Cancel Execution' }).click();

  await expect(page.getByText('Canceled', { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(cancelBtn).not.toBeVisible();
});

// --- Test 2: Cancel dialog dismiss while agent is working ---

test('cancel dialog: dismiss keeps execution running', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'HANG', 'Dismiss cancel test');
  await waitForWorking(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  const cancelBtn = header.getByRole('button', { name: 'Cancel' });
  await expect(cancelBtn).toBeVisible({ timeout: 10000 });

  await cancelBtn.click();
  await expect(page.getByText('Cancel this execution?')).toBeVisible();
  await page.getByRole('button', { name: 'Keep Running' }).click();

  await expect(page.getByText('Cancel this execution?')).not.toBeVisible();
  await expect(cancelBtn).toBeVisible();
});

// --- Test 3: Re-run execution with prefill ---

test('re-run: opens modal with prefilled data from terminal execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'Rerun source');
  await waitForTerminal(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  const rerunBtn = header.getByRole('button', { name: 'Re-run' });
  await expect(rerunBtn).toBeVisible({ timeout: 10000 });

  await rerunBtn.click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Re-run Execution')).toBeVisible();

  const taskTextarea = dialog.getByRole('textbox', { name: 'Task' });
  await expect(taskTextarea).toHaveValue('EXIT_1');

  const titleInput = dialog.getByRole('textbox', { name: /title/i });
  await expect(titleInput).toHaveValue('Re-run: Rerun source');
});

// --- Test 4: Completion summary ---

test('completion summary shows for terminal executions', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'Summary test');
  await waitForTerminal(execId);

  await page.goto(`/#/execution/${execId}`);

  const summary = page.locator('.completion-summary');
  await expect(summary).toBeVisible({ timeout: 10000 });

  await expect(summary).toContainText('Failed at');
  await expect(summary).toContainText('Elapsed');
  await expect(summary).toContainText('session');
});

// --- Test 5: ToolCallCard renderer in Chat view ---

test('tool_call renders as ToolCallCard in chat view', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Tool card test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // StatusBadge shows "Turn Complete" for input-required without pending questions
  await expect(page.locator('.badge').getByText('Turn Complete')).toBeVisible({ timeout: 10000 });

  await page.getByRole('tab', { name: 'Chat' }).click();

  const toolCard = page.locator('.tool-card').first();
  await expect(toolCard).toBeVisible({ timeout: 15000 });
  await expect(toolCard).toContainText('Read file');

  // Attention banner should NOT show for turn-complete executions (no pending questions)
  await page.goto('/');
  await expect(page.locator('.attention-banner')).not.toBeVisible({ timeout: 5000 });
});

// --- Test 6: ThinkingBlock renderer ---

test('thinking renders as collapsible ThinkingBlock in chat view', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_THOUGHT', 'Thinking test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const thinkingBlock = page.locator('.thinking-block').first();
  await expect(thinkingBlock).toBeVisible({ timeout: 15000 });
  await expect(thinkingBlock).toContainText('Thinking...');

  await thinkingBlock.locator('.thinking-header').click();

  const thinkingText = thinkingBlock.locator('.thinking-text');
  await expect(thinkingText).toBeVisible();
  await expect(thinkingText).toContainText('analyze the code structure');
});

// --- Test 7: Error rendering for failed execution ---

test('failed execution shows ErrorPanel in chat view', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'Error test');
  await waitForTerminal(execId, 20000);

  await page.goto(`/#/execution/${execId}`);

  await expect(page.getByText('Failed', { exact: true })).toBeVisible({ timeout: 10000 });

  await page.getByRole('tab', { name: 'Chat' }).click();

  const errorPanel = page.locator('.error-panel');
  await expect(errorPanel).toBeVisible({ timeout: 10000 });
  await expect(errorPanel).toContainText('Error');

  await page.getByRole('tab', { name: 'Log' }).click();
  const errorEntry = page.locator('.timeline-entry.error-entry');
  await expect(errorEntry).toBeVisible({ timeout: 5000 });
  await expect(errorEntry).toContainText('failed');
});

// --- Test 8: SSE fallback ---

test('SSE graceful fallback: events load via polling when SSE unavailable', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'SSE test');
  await waitForTurnEnd(execId);

  // Block SSE so the frontend must fall back to polling
  await page.route('**/events/stream', route => route.abort());

  await page.goto(`/#/execution/${execId}`);

  await expect(page.locator('.timeline-entry').first()).toBeVisible({ timeout: 15000 });

  const count = await page.locator('.timeline-entry').count();
  expect(count).toBeGreaterThan(0);
});
