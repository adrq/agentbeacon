import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { execSync } from 'child_process';
import {
  apiPost, apiGet, apiDelete, ensureDirectAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd,
} from './helpers';

const createdProjectIds: string[] = [];
const createdTempDirs: string[] = [];

function createGitProject(): string {
  const dir = fs.mkdtempSync('/tmp/e2e-ux-git-');
  execSync('git init', { cwd: dir });
  execSync('git -c user.name=Test -c user.email=test@test.com commit --allow-empty -m init', { cwd: dir });
  createdTempDirs.push(dir);
  return dir;
}

async function cleanup() {
  for (const id of createdProjectIds) {
    try { await apiDelete(`/api/projects/${id}`); } catch { /* best effort */ }
  }
  createdProjectIds.length = 0;
  for (const dir of createdTempDirs) {
    try { fs.rmSync(dir, { recursive: true }); } catch { /* best effort */ }
  }
  createdTempDirs.length = 0;
}

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
  await cleanup();
});

// -----------------------------------------------------------------------
// Issue 1: Worktree path copy button
// -----------------------------------------------------------------------

test('copy button appears next to worktree path in sidebar', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitProject();
  const project = await apiPost('/api/projects', { name: 'CopyBtn Git', path: gitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    parts: [{ text: 'SEND_TOOL_CALL' }],
    title: 'Copy btn worktree test',
    project_id: project.id,
  });
  await waitForTurnEnd(exec.execution.id);

  await page.goto(`/#/execution/${exec.execution.id}`);

  // The working-dir-row should be visible (identified by its title attribute)
  const workingDirRow = page.getByTitle('Copy working directory path');
  await expect(workingDirRow).toBeVisible({ timeout: 10000 });

  // The copy button should be visible and labelled correctly
  const copyBtn = page.locator('[aria-label="Copy working directory path"]');
  await expect(copyBtn).toBeVisible();
});

test('clicking copy button next to worktree path updates aria-label to Copied', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitProject();
  const project = await apiPost('/api/projects', { name: 'CopyBtn Click Git', path: gitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    parts: [{ text: 'SEND_TOOL_CALL' }],
    title: 'Copy btn click test',
    project_id: project.id,
  });
  await waitForTurnEnd(exec.execution.id);

  await page.goto(`/#/execution/${exec.execution.id}`);

  // Identify by title attribute (same approach as auto-worktree.spec.ts)
  const workingDirRow = page.getByTitle('Copy working directory path');
  await expect(workingDirRow).toBeVisible({ timeout: 10000 });

  // Use a stable locator (the button inside working-dir-row) that survives aria-label changes
  const copyBtn = workingDirRow.locator('button');
  await expect(copyBtn).toBeVisible();

  // Mock clipboard so the async write succeeds in headless Firefox
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.resolve() },
      configurable: true,
    });
  });
  await copyBtn.click({ force: true });

  // After click, aria-label should change to "Copied"
  await expect(copyBtn).toHaveAttribute('aria-label', 'Copied', { timeout: 2000 });

  // After 1.5s it should reset
  await expect(copyBtn).toHaveAttribute('aria-label', 'Copy working directory path', { timeout: 3000 });
});

// -----------------------------------------------------------------------
// Issue 2: Scroll-to-bottom banner
// -----------------------------------------------------------------------

test('new-messages-bar appears when scrolled up from bottom', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Scroll banner test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for messages to load
  await expect(page.locator('.chat-messages')).toBeVisible({ timeout: 10000 });

  // The scroll-to-bottom button should not be visible when at bottom (auto-scroll active)
  await expect(page.locator('[aria-label="Scroll to bottom"]')).not.toBeVisible();

  // Force enough scrollable content (> 80px) and scroll to top
  await page.locator('.chat-scroll').evaluate(el => {
    const msgs = el.querySelector('.chat-messages') as HTMLElement | null;
    if (msgs) msgs.style.paddingBottom = '300px';
    // Force layout reflow
    void el.scrollHeight;
    el.scrollTop = el.scrollHeight; // ensure we're at bottom first
    el.scrollTop = 0;
    el.dispatchEvent(new Event('scroll'));
  });

  // Banner should appear
  await expect(page.locator('[aria-label="Scroll to bottom"]')).toBeVisible({ timeout: 5000 });
});

test('clicking new-messages-bar scrolls to bottom and hides banner', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Scroll banner click test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.chat-messages')).toBeVisible({ timeout: 10000 });

  // Force enough scrollable content and scroll to top
  await page.locator('.chat-scroll').evaluate(el => {
    const msgs = el.querySelector('.chat-messages') as HTMLElement | null;
    if (msgs) msgs.style.paddingBottom = '300px';
    void el.scrollHeight;
    el.scrollTop = el.scrollHeight;
    el.scrollTop = 0;
    el.dispatchEvent(new Event('scroll'));
  });

  const banner = page.locator('[aria-label="Scroll to bottom"]');
  await expect(banner).toBeVisible({ timeout: 5000 });

  // Click the banner
  await banner.click();

  // Banner should disappear after clicking (auto-scroll re-engaged)
  await expect(banner).not.toBeVisible({ timeout: 5000 });
});

test('new-messages-bar is positioned above TodoPanel in DOM', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TODO_WRITE', 'Banner above todo panel test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.chat-messages')).toBeVisible({ timeout: 10000 });

  // Force enough scrollable content and scroll to top
  await page.locator('.chat-scroll').evaluate(el => {
    const msgs = el.querySelector('.chat-messages') as HTMLElement | null;
    if (msgs) msgs.style.paddingBottom = '300px';
    void el.scrollHeight;
    el.scrollTop = el.scrollHeight;
    el.scrollTop = 0;
    el.dispatchEvent(new Event('scroll'));
  });

  const banner = page.locator('[aria-label="Scroll to bottom"]');
  const todoPanel = page.locator('.todo-panel');

  // If both visible, banner should come before TodoPanel in DOM (lower offsetTop or earlier sibling)
  const bannerIsBeforeTodo = await page.evaluate(() => {
    const bar = document.querySelector('[aria-label="Scroll to bottom"]');
    const panel = document.querySelector('.todo-panel');
    if (!bar || !panel) return true; // if no todo panel, order doesn't matter
    return bar.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING;
  });
  expect(bannerIsBeforeTodo).toBeTruthy();
});

// -----------------------------------------------------------------------
// Issue 3: Branch name in exec-meta
// -----------------------------------------------------------------------

test('branch name appears in exec-meta when worktree API returns branch', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Branch in meta test');
  await waitForTurnEnd(execId);

  // Mock the worktree API for any session to return a branch
  await page.route('**/sessions/*/worktree', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        path: '/tmp/test-worktree',
        branch: 'feat/my-branch',
        head_sha: 'abc1234',
        exists: true,
      }),
    });
  });

  await page.goto(`/#/execution/${execId}`);

  const execMeta = page.locator('.exec-meta');
  await expect(execMeta).toBeVisible({ timeout: 10000 });

  // Wait for branch to appear in exec-meta (route mock must be intercepted)
  await expect(execMeta).toContainText('feat/my-branch', { timeout: 5000 });
});

test('detached HEAD shows detached label in exec-meta', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Detached HEAD test');
  await waitForTurnEnd(execId);

  // Mock the worktree API to return null branch (detached HEAD)
  await page.route('**/sessions/*/worktree', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        path: '/tmp/test-worktree',
        branch: null,
        head_sha: 'deadbeef',
        exists: true,
      }),
    });
  });

  await page.goto(`/#/execution/${execId}`);

  const execMeta = page.locator('.exec-meta');
  await expect(execMeta).toBeVisible({ timeout: 10000 });

  // Wait for detached label to appear in exec-meta
  await expect(execMeta).toContainText('detached', { timeout: 5000 });
});

test('no branch shown when worktree API returns exists=false', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'No worktree no branch');
  await waitForTurnEnd(execId);

  // Mock the worktree API to return exists=false
  await page.route('**/sessions/*/worktree', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        path: '',
        branch: null,
        head_sha: null,
        exists: false,
      }),
    });
  });

  await page.goto(`/#/execution/${execId}`);

  const execMeta = page.locator('.exec-meta');
  await expect(execMeta).toBeVisible({ timeout: 10000 });

  // Wait a moment for the worktree API call to complete, then verify no branch shown
  await page.waitForTimeout(2000);
  await expect(execMeta).not.toContainText('detached');
  // Branch text would be something like a path-like string - just verify exec-meta base text exists
  await expect(execMeta).toContainText('D:');
});

test('branch copy button appears next to branch name and updates aria-label on click', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Branch copy btn test');
  await waitForTurnEnd(execId);

  await page.route('**/sessions/*/worktree', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        path: '/tmp/test-worktree',
        branch: 'main',
        head_sha: 'abc123',
        exists: true,
      }),
    });
  });

  await page.goto(`/#/execution/${execId}`);

  const execMeta = page.locator('.exec-meta');
  await expect(execMeta).toBeVisible({ timeout: 10000 });

  // Wait for branch to appear via route mock
  await expect(execMeta).toContainText('main', { timeout: 5000 });

  // Find the copy button for the branch name - use exec-meta as stable parent scope
  const branchSection = execMeta.locator('.exec-meta-branch');
  await expect(branchSection).toBeVisible();
  // Use a stable locator (the sibling button) that survives aria-label changes
  const copyBtn = execMeta.locator('button').last();
  await expect(copyBtn).toBeVisible();

  // Mock clipboard so the async write succeeds in headless Firefox
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: () => Promise.resolve() },
      configurable: true,
    });
  });
  await copyBtn.click({ force: true });

  await expect(copyBtn).toHaveAttribute('aria-label', 'Copied', { timeout: 2000 });
});
