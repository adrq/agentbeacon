import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: 6 consecutive tool calls collapse into a single tool-stream ---

test('6 consecutive tool calls collapse into a single tool-stream', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_STREAM', 'Tool stream test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const stream = page.locator('.tool-stream');
  await expect(stream).toBeVisible({ timeout: 10000 });
  await expect(stream).toHaveCount(1);

  // No standalone tool-group cards should be visible (all 6 are inside the stream)
  const standaloneToolGroups = page.locator('.chat-row > .tool-group');
  await expect(standaloneToolGroups).toHaveCount(0);
});

// --- Test 2: Tool-stream summary shows type breakdown ---

test('tool-stream summary shows type breakdown', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_STREAM', 'Summary breakdown test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const summary = page.locator('.tool-stream-summary');
  await expect(summary).toBeVisible({ timeout: 10000 });
  await expect(summary).toContainText('6 tool calls');
  await expect(summary).toContainText('4 WebSearch');
  await expect(summary).toContainText('1 WebFetch');
  await expect(summary).toContainText('1 Read');
});

// --- Test 3: Tool-stream expands to show individual log lines ---

test('tool-stream expands to show individual log lines', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_STREAM', 'Expand test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const summary = page.locator('.tool-stream-summary');
  await expect(summary).toBeVisible({ timeout: 10000 });
  await summary.click();

  const logLines = page.locator('.ts-log-line');
  await expect(logLines).toHaveCount(6);
});

// --- Test 4: Tool-stream log line expands to show ToolGroup detail ---

test('tool-stream log line expands to show ToolGroup detail', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_STREAM', 'Detail test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await page.locator('.tool-stream-summary').click();
  await page.locator('.ts-log-line-header').first().click();

  const detail = page.locator('.ts-line-detail');
  await expect(detail).toBeVisible({ timeout: 5000 });
  await expect(detail.locator('.tool-group')).toBeVisible();
});

// --- Test 5: Tool-stream summary has correct aria-expanded ---

test('tool-stream summary has correct aria-expanded', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_STREAM', 'A11y test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const summary = page.locator('.tool-stream-summary');
  await expect(summary).toBeVisible({ timeout: 10000 });
  await expect(summary).toHaveAttribute('aria-expanded', 'false');

  await summary.click();
  await expect(summary).toHaveAttribute('aria-expanded', 'true');
});

// --- Test 6: 1-2 consecutive tool calls render as individual tool-group cards ---

test('1-2 consecutive tool calls render as individual tool-group cards', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_GROUP', 'No stream test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // A regular tool-group should be visible (confirms page has rendered)
  await expect(page.locator('.tool-group')).toBeVisible({ timeout: 10000 });

  // No tool-stream should exist (page is rendered, so this is not vacuously true)
  await expect(page.locator('.tool-stream')).toHaveCount(0);
});
