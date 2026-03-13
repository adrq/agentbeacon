import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { execSync } from 'child_process';
import { apiPost, apiGet, apiDelete, ensureDirectAgent } from './helpers';

const createdProjectIds: string[] = [];
const createdTempDirs: string[] = [];

function createGitProject(): string {
  const dir = fs.mkdtempSync('/tmp/e2e-git-');
  execSync('git init', { cwd: dir });
  execSync('git -c user.name=Test -c user.email=test@test.com commit --allow-empty -m init', { cwd: dir });
  createdTempDirs.push(dir);
  return dir;
}

function createNonGitProject(): string {
  const dir = fs.mkdtempSync('/tmp/e2e-nogit-');
  createdTempDirs.push(dir);
  return dir;
}

async function getRootSession(execId: string): Promise<any> {
  const detail = await apiGet(`/api/executions/${execId}`);
  return detail.sessions.find((s: any) => s.parent_session_id === null);
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

test.afterEach(async () => {
  await cleanup();
});

test('auto worktree execution shows working directory', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitProject();
  const project = await apiPost('/api/projects', { name: 'WT Git', path: gitPath });
  createdProjectIds.push(project.id);

  // Create execution via API (no branch = auto-worktree for git project)
  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    prompt: 'test auto worktree',
    title: 'Auto WT Test',
    project_id: project.id,
  });

  const rootSession = await getRootSession(exec.execution.id);
  expect(rootSession.worktree_path).toBeTruthy();

  // Navigate to execution detail and verify path is shown
  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByTitle('Copy working directory path')).toBeVisible({ timeout: 5000 });
});

test('non git project shows no working directory', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const noGitPath = createNonGitProject();
  const project = await apiPost('/api/projects', { name: 'WT NoGit', path: noGitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    prompt: 'test no worktree',
    title: 'No WT Test',
    project_id: project.id,
  });

  const rootSession = await getRootSession(exec.execution.id);
  expect(rootSession.worktree_path).toBeNull();

  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByRole('heading', { name: 'No WT Test' })).toBeVisible({ timeout: 5000 });
  // Working directory button should not exist
  await expect(page.getByTitle('Copy working directory path')).not.toBeVisible();
});

test('branch field in advanced section', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitProject();
  const noGitPath = createNonGitProject();

  // Create both projects before opening form so they appear in dropdown
  const project = await apiPost('/api/projects', { name: 'WT Branch', path: gitPath });
  createdProjectIds.push(project.id);
  const noGitProject = await apiPost('/api/projects', { name: 'WT NoGit2', path: noGitPath });
  createdProjectIds.push(noGitProject.id);

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  await expect(page.getByRole('heading', { name: 'New Execution' })).toBeVisible({ timeout: 5000 });

  // Select git project
  await page.locator('.form-panel').getByLabel('Project').selectOption(project.id);

  // Branch field should now be visible (advanced section is always visible) with correct placeholder
  const branchInput = page.getByLabel('Branch');
  await expect(branchInput).toBeVisible();
  await expect(branchInput).toHaveAttribute('placeholder', 'Optional: explicit branch name');

  // Hint text present
  await expect(page.getByText('Leave blank for automatic isolated copy')).toBeVisible();

  // Switch to non-git project: branch field should disappear
  await page.locator('.form-panel').getByLabel('Project').selectOption(noGitProject.id);
  await expect(page.getByLabel('Branch')).not.toBeVisible();
});

test('explicit branch override', async () => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitProject();
  const project = await apiPost('/api/projects', { name: 'WT ExplBranch', path: gitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    prompt: 'test explicit branch',
    title: 'Explicit Branch',
    project_id: project.id,
    branch: 'test-feature',
  });

  const rootSession = await getRootSession(exec.execution.id);
  expect(rootSession.worktree_path).toBeTruthy();

  // Verify named branch was created
  const branches = execSync('git branch --list "beacon/test-feature"', { cwd: gitPath })
    .toString().trim();
  expect(branches).toContain('beacon/test-feature');
});

test('explicit cwd overrides auto worktree', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitProject();
  const project = await apiPost('/api/projects', { name: 'WT CWD', path: gitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    prompt: 'test cwd override',
    title: 'CWD Override',
    project_id: project.id,
    cwd: '/tmp',
  });

  // Explicit cwd means no worktree
  const rootSession = await getRootSession(exec.execution.id);
  expect(rootSession.worktree_path).toBeNull();

  await page.goto(`/#/execution/${exec.execution.id}`);
  await expect(page.getByRole('heading', { name: 'CWD Override' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByTitle('Copy working directory path')).not.toBeVisible();
});

test('working directory copy button exists', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const gitPath = createGitProject();
  const project = await apiPost('/api/projects', { name: 'WT Copy', path: gitPath });
  createdProjectIds.push(project.id);

  const exec = await apiPost('/api/executions', {
    root_agent_id: agent.id,
    agent_ids: [agent.id],
    prompt: 'test copy',
    title: 'Copy Test',
    project_id: project.id,
  });

  await page.goto(`/#/execution/${exec.execution.id}`);
  const copyBtn = page.getByTitle('Copy working directory path');
  await expect(copyBtn).toBeVisible({ timeout: 5000 });
  // Verify the button text contains the worktree path
  await expect(copyBtn).toContainText('.agentbeacon/projects/');
});
