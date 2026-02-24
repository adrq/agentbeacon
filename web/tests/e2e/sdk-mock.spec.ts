import { test, expect } from '@playwright/test';
import {
  ensureClaudeAgent, ensureCopilotAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Claude mock renders all SDK content types ---

test('claude mock: full showcase renders all SDK content types', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Analyze the codebase', 'Claude mock showcase');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);

  // Log view: thinking (ellipsis), tool calls (gear), text
  const thinkingEntry = page.locator('.timeline-entry').filter({ hasText: 'Let me analyze the codebase' });
  await expect(thinkingEntry).toBeVisible({ timeout: 10000 });
  await expect(thinkingEntry.locator('.ev-icon')).toContainText('\u22EF');

  // Tool entries show gear icon — use entries that ONLY match the tool name, not text content
  const gearEntries = page.locator('.timeline-entry').filter({
    has: page.locator('.ev-icon:has-text("\u2699")'),
  });
  await expect(gearEntries.first()).toBeVisible();
  // 3 tool_use (Read, Grep, Edit) + 3 tool_result = 6 gear entries
  await expect(gearEntries).toHaveCount(6);

  // Chat view: ThinkingBlock, ToolCallCard, ToolResultCard, markdown
  await page.getByRole('tab', { name: 'Chat' }).click();

  const thinkingBlock = page.locator('.thinking-block').first();
  await expect(thinkingBlock).toBeVisible({ timeout: 10000 });
  await expect(thinkingBlock).toContainText('Thinking...');

  const toolCard = page.locator('.tool-card').first();
  await expect(toolCard).toBeVisible();

  // Markdown: heading, table, code block with syntax highlighting
  const markdown = page.locator('.agent-bubble .markdown-body').last();
  await expect(markdown.locator('h1')).toBeVisible();
  await expect(markdown.locator('h1')).toContainText('Changes Complete');
  await expect(markdown.locator('table')).toBeVisible();
  await expect(markdown.locator('pre')).toBeVisible();
  await expect(page.locator('.shiki').first()).toBeVisible({ timeout: 15000 });
});

// --- Test 2: Copilot mock renders all SDK content types ---

test('copilot mock: full showcase renders all SDK content types', async ({ page }) => {
  const agent = await ensureCopilotAgent();
  const { execId } = await createExecution(agent.id, 'Fix the failing tests', 'Copilot mock showcase');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // ThinkingBlock
  const thinkingBlock = page.locator('.thinking-block').first();
  await expect(thinkingBlock).toBeVisible({ timeout: 10000 });
  await expect(thinkingBlock).toContainText('Thinking...');

  // ToolCallCard (Bash and Read)
  const bashCard = page.locator('.tool-card').filter({ hasText: 'Bash' });
  await expect(bashCard).toBeVisible();

  const readCard = page.locator('.tool-card').filter({ hasText: 'Read' });
  await expect(readCard).toBeVisible();

  // Text message with code block
  const agentBubble = page.locator('.agent-bubble').last();
  await expect(agentBubble).toContainText('Fixed the failing test');
  await expect(agentBubble.locator('code')).toBeVisible();
});

// --- Test 3: Claude mock execution completes successfully ---

test('claude mock: execution completes successfully', async () => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Run analysis', 'Claude completion test');
  const status = await waitForTurnEnd(execId, 20000);

  // Mock leaves execution at input-required (promptStream waits for next command).
  // This is correct — it means the showcase scenario ran to completion.
  expect(status).toBe('input-required');
});

// --- Test 4: Copilot mock execution completes successfully ---

test('copilot mock: execution completes successfully', async () => {
  const agent = await ensureCopilotAgent();
  const { execId } = await createExecution(agent.id, 'Fix tests', 'Copilot completion test');
  const status = await waitForTurnEnd(execId, 20000);

  expect(status).toBe('input-required');
});
