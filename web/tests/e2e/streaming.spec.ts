import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureShowcaseAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd, waitForWorkerPickup,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Streaming chunks accumulate into single agent message ---

test('streaming chunks accumulate into single agent message', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Stream chunks test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for agent prose to appear
  await expect(page.locator('.agent-prose').first()).toBeVisible({ timeout: 10000 });

  // Should be exactly 1 agent-prose block (not 5 separate blocks)
  const agentBlocks = page.locator('.agent-prose');
  await expect(agentBlocks).toHaveCount(1);
});

// --- Test 2: Accumulated message contains all chunk content ---

test('accumulated message contains all chunk content', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Chunk content test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.first()).toBeVisible({ timeout: 10000 });

  const text = await markdown.first().textContent() ?? '';
  expect(text).toContain('Analysis Results');
  expect(text).toContain('Architecture');
  expect(text).toContain('Performance');
  expect(text).toContain('preliminary');
});

// --- Test 3: Non-streaming single message renders as one block ---

test('non-streaming single message renders as one block', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Non-streaming test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.agent-prose').first()).toBeVisible({ timeout: 10000 });

  const agentBlocks = page.locator('.agent-prose');
  await expect(agentBlocks).toHaveCount(1);

  const text = await page.locator('.agent-prose .markdown-body').first().textContent() ?? '';
  expect(text).toContain('Analysis Report');
});

// --- Test 4: Tool calls break text accumulation ---

test('tool calls break text accumulation', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Test interleave', 'Interleave test');
  await waitForWorkerPickup(execId, 15000);
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for final markdown to render
  await expect(page.locator('.agent-prose .markdown-body h1')).toBeVisible({ timeout: 15000 });

  // Should have multiple agent-prose blocks (text broken by tool calls)
  const agentBlocks = page.locator('.agent-prose');
  const count = await agentBlocks.count();
  expect(count).toBeGreaterThanOrEqual(2);

  // Should have tool groups between agent text blocks
  await expect(page.locator('.tool-group').first()).toBeVisible();
});

// --- Test 5: Markdown renders correctly in accumulated block ---

test('markdown renders correctly in accumulated block', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Markdown render test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.first()).toBeVisible({ timeout: 10000 });

  // Verify rendered HTML elements (not raw markdown)
  await expect(markdown.locator('h1')).toBeVisible();
  await expect(markdown.locator('h1')).toContainText('Analysis Results');
  await expect(markdown.locator('strong').first()).toBeVisible();
  await expect(markdown.locator('blockquote')).toBeVisible();
});
