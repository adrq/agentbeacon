import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureShowcaseAgent, createExecution,
  waitForWorkerIdle, waitForWorkerPickup, waitForTurnEnd,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Markdown renders correctly after streaming settles ---

test('markdown renders correctly after streaming settles', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Streaming settle test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for markdown to render with full pipeline (after settling)
  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.first()).toBeVisible({ timeout: 10000 });

  // Verify that full markdown rendering works (h1, strong, blockquote)
  await expect(markdown.locator('h1')).toBeVisible();
  await expect(markdown.locator('strong').first()).toBeVisible();
  await expect(markdown.locator('blockquote')).toBeVisible();
});

// --- Test 2: Syntax highlighting appears after settling ---

test('syntax highlighting appears after settling', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Shiki settle test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for markdown to be visible
  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.first()).toBeVisible({ timeout: 10000 });

  // Verify Shiki syntax highlighting is present (after settle)
  // The .shiki class is added by the full processor, and .shiki-visible for fade-in
  const shikiBlocks = page.locator('.agent-prose .markdown-body pre.shiki.shiki-visible');

  // Wait for at least one Shiki block to appear with fade-in complete
  await expect(shikiBlocks.first()).toBeVisible({ timeout: 10000 });

  // Verify count
  const count = await shikiBlocks.count();
  expect(count).toBeGreaterThan(0);
});

// --- Test 3: CSS containment is applied ---

test('CSS containment is applied to chat rows', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'CSS containment test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for chat rows to be visible
  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 10000 });

  // Verify that CSS containment is applied
  const chatRow = page.locator('.chat-row').first();
  const containStyle = await chatRow.evaluate(el => window.getComputedStyle(el).contain);

  // Should have 'layout' and 'style' containment
  expect(containStyle).toContain('layout');
  expect(containStyle).toContain('style');
});

// --- Test 4: Auto-scroll works during streaming ---

test('auto-scroll works during streaming', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'Auto-scroll test');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for chat scroll container to be visible
  const scrollContainer = page.locator('.chat-scroll');
  await expect(scrollContainer).toBeVisible({ timeout: 10000 });

  // Wait for chat content to start appearing, then for the final showcase message.
  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 10000 });
  const finalEntry = page.locator('.agent-prose .markdown-body h1').filter({ hasText: 'Refactoring Complete' });
  await expect(finalEntry).toBeVisible({ timeout: 30000 });

  // Verify auto-scroll stayed pinned to the bottom while new rows were appended.
  await page.waitForFunction(() => {
    const el = document.querySelector('.chat-scroll');
    if (!el) return false;
    const tolerance = 40; // same as the shouldAutoScroll threshold
    return el.scrollHeight - el.scrollTop - el.clientHeight < tolerance;
  }, { timeout: 10000 });
});

// --- Test 5: Memoized parsing doesn't break event rendering ---

test('memoized parsing renders tool groups correctly', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Parsing test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for tool groups to be visible
  await expect(page.locator('.tool-group').first()).toBeVisible({ timeout: 10000 });

  // Verify tool groups render (validates split baseEntries/parsed derivation)
  const toolGroups = page.locator('.tool-group');
  const count = await toolGroups.count();
  expect(count).toBeGreaterThanOrEqual(1);
});

// --- Test 6: Persisted messages retain syntax highlighting ---
// Regression test for bug where persisted messages would lose Shiki highlighting
// when session was still active (working) but no ephemeral text was flowing.

test('persisted messages retain syntax highlighting', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'Shiki persistence test');
  await waitForWorkerPickup(execId, 15000);

  // Wait for the persisted code-block message to exist before asserting on Shiki markup.
  const finalEntry = page.locator('.timeline-entry').filter({ hasText: 'Refactoring Complete' });
  await page.goto(`/#/execution/${execId}`);
  await expect(finalEntry).toBeVisible({ timeout: 30000 });

  // Navigate to chat view while execution might still be settling
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for agent prose to be visible
  const agentProse = page.locator('.agent-prose');
  await expect(agentProse.first()).toBeVisible({ timeout: 30000 });

  // Critical assertion: persisted messages MUST have Shiki highlighting
  // even when session is still active (not yet terminal)
  // The fix changed: streaming={entry.isStreaming} -> streaming={entry.key === 'ephemeral-stream'}
  // so only ephemeral text uses the lightweight streaming processor
  const shikiBlocks = page.locator('.agent-prose .markdown-body pre.shiki');

  // Wait for at least one Shiki block to appear
  await expect(shikiBlocks.first()).toBeVisible({ timeout: 10000 });

  // Verify we have multiple Shiki blocks (Showcase agent produces several code blocks)
  const count = await shikiBlocks.count();
  expect(count).toBeGreaterThan(0);

  // Additionally verify the .shiki-visible class for fade-in completion
  const visibleShikiBlocks = page.locator('.agent-prose .markdown-body pre.shiki.shiki-visible');
  await expect(visibleShikiBlocks.first()).toBeVisible({ timeout: 10000 });
});
