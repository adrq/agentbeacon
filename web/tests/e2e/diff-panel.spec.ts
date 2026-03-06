import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { execSync } from 'child_process';
import { apiPost, apiGet, apiDelete, ensureDirectAgent, waitForTurnEnd, waitForWorkerIdle } from './helpers';

const createdProjectIds: string[] = [];
const createdTempDirs: string[] = [];

function createGitRepo(): string {
  const dir = fs.mkdtempSync('/tmp/e2e-diff-');
  execSync('git init', { cwd: dir });
  execSync('git -c user.name=Test -c user.email=test@test.com commit --allow-empty -m init', { cwd: dir });
  createdTempDirs.push(dir);
  return dir;
}

async function createExecutionWithDiff(agentId: string) {
  const gitPath = createGitRepo();
  const project = await apiPost('/api/projects', { name: 'DiffPanel Test', path: gitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    agent_id: agentId,
    prompt: 'STREAM_CHUNKS',
    title: 'Diff Panel Test',
    project_id: project.id,
  });
  await waitForTurnEnd(exec.execution.id);

  // Get worktree path from root session
  const detail = await apiGet(`/api/executions/${exec.execution.id}`);
  const rootSession = detail.sessions.find((s: any) => s.parent_session_id === null);
  const wtPath = rootSession?.worktree_path;

  // Write files into the worktree to produce diffs
  if (wtPath) {
    fs.writeFileSync(`${wtPath}/new-file.ts`, 'export const x = 1;\nexport function greet(name: string) {\n  return `Hello, ${name}!`;\n}\n');
    fs.writeFileSync(`${wtPath}/another.py`, 'def hello():\n    return "world"\n');
    // Stage so they show in git diff HEAD
    execSync('git add new-file.ts another.py', { cwd: wtPath });
  }

  // Complete the execution so it reaches terminal state
  await apiPost(`/api/executions/${exec.execution.id}/complete`, {});

  return { execId: exec.execution.id, projectId: project.id, tmpDir: gitPath, wtPath };
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

test('diff tab appears in execution detail toggle', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'STREAM_CHUNKS',
    title: 'Diff Tab Visible',
    cwd: '/tmp',
  });
  await waitForTurnEnd(exec.execution.id);

  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByRole('heading', { name: 'Diff Tab Visible' })).toBeVisible({ timeout: 10000 });

  // Verify three toggle buttons
  const tabs = page.locator('.view-toggle .toggle-btn');
  await expect(tabs).toHaveCount(3);
  await expect(tabs.nth(0)).toHaveText('Log');
  await expect(tabs.nth(1)).toHaveText('Chat');
  await expect(tabs.nth(2)).toHaveText('Diff');

  // Verify Diff tab has role="tab"
  const diffTab = tabs.nth(2);
  await expect(diffTab).toHaveAttribute('role', 'tab');
});

test('diff tab shows empty state for non-git cwd execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'STREAM_CHUNKS',
    title: 'No WT Diff',
    cwd: '/tmp',
  });
  await waitForTurnEnd(exec.execution.id);

  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByRole('heading', { name: 'No WT Diff' })).toBeVisible({ timeout: 10000 });

  // Click Diff tab
  await page.locator('.toggle-btn', { hasText: 'Diff' }).click();

  // cwd=/tmp is not a git repo, so we get "Not a git repository" or "No worktree"
  await expect(page.locator('.diff-empty')).toBeVisible({ timeout: 5000 });
});

test('diff tab selection persists in localStorage', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'STREAM_CHUNKS',
    title: 'Diff Persist',
    cwd: '/tmp',
  });
  await waitForTurnEnd(exec.execution.id);

  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByRole('heading', { name: 'Diff Persist' })).toBeVisible({ timeout: 10000 });

  // Click Diff tab
  await page.locator('.toggle-btn', { hasText: 'Diff' }).click();
  await expect(page.locator('.toggle-btn.active')).toHaveText('Diff');

  // Verify localStorage
  const stored = await page.evaluate(() => localStorage.getItem('agentbeacon-event-view-mode'));
  expect(stored).toBe('diff');

  // Navigate away and back
  await page.goto('/#/');
  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByRole('heading', { name: 'Diff Persist' })).toBeVisible({ timeout: 10000 });

  // Verify Diff tab is still selected
  await expect(page.locator('.toggle-btn.active')).toHaveText('Diff');

  // Reset to 'log' so other tests aren't affected
  await page.locator('.toggle-btn', { hasText: 'Log' }).click();
});

test('diff renders with summary bar and file list for worktree execution', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecutionWithDiff(agent.id);

  await page.goto(`/#/execution/${execId}`);
  await expect(page.getByRole('heading', { name: 'Diff Panel Test' })).toBeVisible({ timeout: 10000 });

  // Click Diff tab
  await page.locator('.toggle-btn', { hasText: 'Diff' }).click();

  // Verify summary bar renders with file stats
  const summaryBar = page.locator('.diff-summary-bar');
  await expect(summaryBar).toBeVisible({ timeout: 10000 });
  await expect(summaryBar).toContainText('files changed');
  await expect(summaryBar.locator('.diff-stat-add')).toBeVisible();
  await expect(summaryBar.locator('.diff-stat-del')).toBeVisible();

  // Verify file list renders with per-file entries
  const fileList = page.locator('.diff-file-list');
  await expect(fileList).toBeVisible();
  const fileEntries = fileList.locator('.diff-file-entry');
  await expect(fileEntries).toHaveCount(2); // new-file.ts, another.py

  // Verify file entries have status, path, and stats
  const firstEntry = fileEntries.first();
  await expect(firstEntry.locator('.file-status')).toBeVisible();
  await expect(firstEntry.locator('.file-path')).toBeVisible();
  await expect(firstEntry.locator('.file-stats')).toBeVisible();

  // Reset view mode
  await page.locator('.toggle-btn', { hasText: 'Log' }).click();
});

test('diff content has syntax highlighting from diff2html', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecutionWithDiff(agent.id);

  await page.goto(`/#/execution/${execId}`);
  await expect(page.getByRole('heading', { name: 'Diff Panel Test' })).toBeVisible({ timeout: 10000 });

  await page.locator('.toggle-btn', { hasText: 'Diff' }).click();

  // Wait for diff2html content to render (lazy loaded)
  const diffContent = page.locator('.diff-content');
  await expect(diffContent).toBeVisible({ timeout: 10000 });

  // Verify d2h-wrapper is present (diff2html rendered)
  await expect(diffContent.locator('.d2h-wrapper')).toBeVisible({ timeout: 5000 });

  // Verify at least one file wrapper exists
  await expect(diffContent.locator('.d2h-file-wrapper').first()).toBeVisible();

  // Verify syntax highlighting is applied (hljs classes present)
  await expect(diffContent.locator('[class*="hljs"]').first()).toBeVisible();

  // Reset view mode
  await page.locator('.toggle-btn', { hasText: 'Log' }).click();
});

test('clicking file in list scrolls to corresponding diff section', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecutionWithDiff(agent.id);

  await page.goto(`/#/execution/${execId}`);
  await expect(page.getByRole('heading', { name: 'Diff Panel Test' })).toBeVisible({ timeout: 10000 });

  await page.locator('.toggle-btn', { hasText: 'Diff' }).click();

  // Wait for file list and diff content
  await expect(page.locator('.diff-file-list')).toBeVisible({ timeout: 10000 });
  await expect(page.locator('.diff-content .d2h-wrapper')).toBeVisible({ timeout: 5000 });

  // Verify precondition: exactly 2 files (new-file.ts, another.py)
  const fileEntries = page.locator('.diff-file-entry');
  await expect(fileEntries).toHaveCount(2);

  // Click the second file in the file list
  await fileEntries.nth(1).click();
  // Brief wait for smooth scroll
  await page.waitForTimeout(600);
  // The second d2h-file-wrapper should be in view
  const secondFile = page.locator('.d2h-file-wrapper').nth(1);
  await expect(secondFile).toBeInViewport();

  // Reset view mode
  await page.locator('.toggle-btn', { hasText: 'Log' }).click();
});

test('no-changes state when worktree has no modifications', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitRepo();
  const project = await apiPost('/api/projects', { name: 'NoChanges Test', path: gitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'STREAM_CHUNKS',
    title: 'No Changes Diff',
    project_id: project.id,
  });
  await waitForTurnEnd(exec.execution.id);
  await apiPost(`/api/executions/${exec.execution.id}/complete`, {});

  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByRole('heading', { name: 'No Changes Diff' })).toBeVisible({ timeout: 10000 });

  await page.locator('.toggle-btn', { hasText: 'Diff' }).click();

  // Should show "No changes detected"
  await expect(page.locator('.diff-empty')).toContainText('No changes detected', { timeout: 5000 });

  // Reset view mode
  await page.locator('.toggle-btn', { hasText: 'Log' }).click();
});
