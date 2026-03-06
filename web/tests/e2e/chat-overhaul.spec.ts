import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureDemoAgent, ensureClaudeAgent, ensureShowcaseAgent,
  ensureTCLeadAgent, ensureTCChildAgent,
  createExecution,
  waitForWorkerIdle, waitForWorkerPickup, waitForTurnEnd, waitForEvent,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Agent messages render as document flow (no bubble) ---

test('agent messages render as document flow (no bubble)', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Agent prose test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const agentProse = page.locator('.agent-prose');
  await expect(agentProse.first()).toBeVisible({ timeout: 10000 });

  // No agent-bubble class should exist
  await expect(page.locator('.agent-bubble')).not.toBeVisible();

  // Markdown renders inside agent-prose
  await expect(agentProse.locator('.markdown-body h1').first()).toBeVisible();
});

// --- Test 2: User messages retain bubble layout ---

test('user messages retain bubble layout', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Test user bubble', 'User bubble test');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);

  // Wait for question to appear, then answer it to generate a user message
  const main = page.locator('#main-content');
  await expect(main.locator('.question-text')).toContainText('Which approach should I take?', { timeout: 20000 });
  await main.getByRole('radio', { name: /Refactor existing code/ }).click();
  await main.getByRole('button', { name: /Submit/ }).click();

  // Switch to Chat view and verify user bubble
  await page.getByRole('tab', { name: 'Chat' }).click();

  const userBubble = page.locator('.user-bubble');
  await expect(userBubble.first()).toBeVisible({ timeout: 10000 });

  // Verify right-alignment
  const row = page.locator('.user-row').first();
  const justifyContent = await row.evaluate(el => getComputedStyle(el).justifyContent);
  expect(justifyContent).toBe('flex-end');
});

// --- Test 3: Tool call and update grouped into single entry ---

test('tool call and update are grouped into single entry', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_GROUP', 'Tool group test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Should have exactly 1 tool-group (call + update merged)
  const toolGroups = page.locator('.tool-group');
  await expect(toolGroups.first()).toBeVisible({ timeout: 10000 });
  await expect(toolGroups).toHaveCount(1);

  // The grouped entry should contain the tool name and COMPLETED status
  await expect(toolGroups.first()).toContainText('Read config.json');
  await expect(toolGroups.first()).toContainText(/completed/i);
});

// --- Test 4: Tool groups are left-aligned ---

test('tool groups are left-aligned', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Tool alignment test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const toolRow = page.locator('.tool-row').first();
  await expect(toolRow).toBeVisible({ timeout: 10000 });

  const justifyContent = await toolRow.evaluate(el => getComputedStyle(el).justifyContent);
  expect(justifyContent).toBe('flex-start');
});

// --- Test 5: Scroll-to-bottom button appears when scrolled up ---

test('scroll-to-bottom button appears when scrolled up', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'Scroll FAB test');
  await waitForWorkerPickup(execId, 15000);
  await waitForTurnEnd(execId, 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for content to load
  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 15000 });

  // Scroll up to trigger FAB
  await page.evaluate(() => {
    const el = document.querySelector('.chat-scroll');
    if (el) el.scrollTop = 0;
  });

  // FAB should appear
  const fab = page.getByRole('button', { name: 'Scroll to bottom' });
  await expect(fab).toBeVisible({ timeout: 5000 });

  // Click FAB — should scroll to bottom and disappear
  await fab.click();
  await expect(fab).not.toBeVisible({ timeout: 5000 });
});

// --- Test 6: Scroll-to-bottom button has correct aria-label ---

test('scroll-to-bottom button has correct aria-label', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'Scroll aria test');
  await waitForWorkerPickup(execId, 15000);
  await waitForTurnEnd(execId, 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 15000 });

  await page.evaluate(() => {
    const el = document.querySelector('.chat-scroll');
    if (el) el.scrollTop = 0;
  });

  const fab = page.locator('[aria-label="Scroll to bottom"]');
  await expect(fab).toBeVisible({ timeout: 5000 });
});

// --- Test 7: Log view suppresses tool results with matching call ---

test('log view suppresses tool results with matching call', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze the codebase', 'Log suppression test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);

  // Log view (default): count gear icon entries
  // Claude mock sends 3 tool_use + 3 tool_result — after suppression, only 3 gear entries
  const gearEntries = page.locator('.timeline-entry').filter({
    has: page.locator('.ev-icon:has-text("\u2699")'),
  });
  await expect(gearEntries.first()).toBeVisible({ timeout: 10000 });
  await expect(gearEntries).toHaveCount(3);
});

// --- Test 8: Platform events still render as tool-card ---

test('platform events still render as tool-card', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();

  const { execId } = await createExecution(lead.id, 'Turn-complete rendering test', 'TC card test', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Platform event "Child reported" uses .tool-card class (NOT .tool-group)
  const toolCard = page.locator('.tool-card').filter({ hasText: 'Child reported' });
  await expect(toolCard).toBeVisible({ timeout: 10000 });
});
