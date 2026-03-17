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

// --- Test 1: Completed execution shows rendered markdown on fresh page load ---

test('completed execution shows rendered markdown on fresh page load', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Markdown reload test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Assert that markdown is rendered (h1 visible)
  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.locator('h1')).toBeVisible({ timeout: 10000 });

  // Critical assertion: .markdown-plain should NOT appear (means rendering failed)
  const plainCount = await page.locator('.agent-prose .markdown-plain').count();
  expect(plainCount).toBe(0);

  // Verify no raw markdown syntax in the text
  const text = await markdown.first().textContent() ?? '';
  expect(text).not.toContain('# ');
  expect(text).not.toContain('## ');
  expect(text).not.toContain('**');
  expect(text).not.toContain('```');
  expect(text).not.toContain('|---|');
});

// --- Test 2: Page reload preserves markdown rendering ---

test('page reload preserves markdown rendering', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Markdown reload test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Verify markdown is rendered before reload
  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.locator('h1')).toBeVisible({ timeout: 10000 });

  let plainCount = await page.locator('.agent-prose .markdown-plain').count();
  expect(plainCount).toBe(0);

  // Reload the page
  await page.reload();
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Verify markdown is still rendered after reload
  await expect(markdown.locator('h1')).toBeVisible({ timeout: 10000 });

  plainCount = await page.locator('.agent-prose .markdown-plain').count();
  expect(plainCount).toBe(0);

  // Verify no raw markdown syntax after reload
  const textAfter = await markdown.first().textContent() ?? '';
  expect(textAfter).not.toContain('# ');
  expect(textAfter).not.toContain('## ');
  expect(textAfter).not.toContain('**');
  expect(textAfter).not.toContain('```');
  expect(textAfter).not.toContain('|---|');
});

// --- Test 3: Completed streamed execution renders markdown on reload ---

test('completed streamed execution renders markdown on reload', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Streamed markdown reload test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Verify markdown elements are rendered
  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.locator('h1')).toBeVisible({ timeout: 10000 });
  await expect(markdown.locator('strong').first()).toBeVisible();
  await expect(markdown.locator('blockquote')).toBeVisible();

  // Verify no raw markdown syntax before reload
  const textBefore = await markdown.first().textContent() ?? '';
  expect(textBefore).not.toContain('# ');
  expect(textBefore).not.toContain('**');
  expect(textBefore).not.toContain('> ');

  // Reload the page
  await page.reload();
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Verify markdown elements are still rendered after reload
  await expect(markdown.locator('h1')).toBeVisible({ timeout: 10000 });
  await expect(markdown.locator('strong').first()).toBeVisible();
  await expect(markdown.locator('blockquote')).toBeVisible();

  // Critical assertion: no .markdown-plain fallback
  const plainCount = await page.locator('.agent-prose .markdown-plain').count();
  expect(plainCount).toBe(0);

  // Verify no raw markdown syntax after reload
  const textAfter = await markdown.first().textContent() ?? '';
  expect(textAfter).not.toContain('# ');
  expect(textAfter).not.toContain('**');
  expect(textAfter).not.toContain('> ');
});

// --- Test 4: Shiki highlighting survives page reload ---

test('Shiki highlighting survives page reload', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Shiki reload test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for Shiki highlighting to be visible
  const shikiBlock = page.locator('.agent-prose .markdown-body pre.shiki.shiki-visible');
  await expect(shikiBlock.first()).toBeVisible({ timeout: 10000 });

  // Reload the page
  await page.reload();
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Verify Shiki highlighting is still present after reload
  await expect(shikiBlock.first()).toBeVisible({ timeout: 10000 });
});
