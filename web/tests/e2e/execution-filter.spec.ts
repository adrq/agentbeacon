import { test, expect } from '@playwright/test';
import { apiPost, apiDelete, ensureDirectAgent, waitForWorkerIdle } from './helpers';

const cleanupProjectIds: string[] = [];

test.afterEach(async () => {
  await waitForWorkerIdle();
  for (const id of cleanupProjectIds) {
    try { await apiDelete(`/api/projects/${id}`); } catch { /* best effort */ }
  }
  cleanupProjectIds.length = 0;
});

test('filter execution list by project', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const projectA = await apiPost('/api/projects', { name: 'Filter A', path: '/tmp' });
  const projectB = await apiPost('/api/projects', { name: 'Filter B', path: '/tmp' });
  cleanupProjectIds.push(projectA.id, projectB.id);

  const execA = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'Task for A',
    title: 'Exec A',
    project_id: projectA.id,
  });
  const execB = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'Task for B',
    title: 'Exec B',
    project_id: projectB.id,
  });
  await page.goto('/');

  const execList = page.locator('.exec-list, .execution-list, main');

  const execAItem = execList.locator('.exec-item', { hasText: 'Exec A' }).first();
  const execBItem = execList.locator('.exec-item', { hasText: 'Exec B' }).first();

  await expect(execAItem).toBeVisible({ timeout: 10000 });
  await expect(execBItem).toBeVisible();

  const filter = page.getByLabel('Filter by project');
  await filter.selectOption(projectA.id);

  await expect(execAItem).toBeVisible({ timeout: 10000 });
  await expect(execBItem).not.toBeVisible({ timeout: 10000 });

  await filter.selectOption('');

  await expect(execAItem).toBeVisible({ timeout: 10000 });
  await expect(execBItem).toBeVisible({ timeout: 10000 });
});
