import { test, expect } from '@playwright/test';
import { apiPost, ensureDirectAgent } from './helpers';

test('create execution with project selected', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const project = await apiPost('/api/projects', { name: 'Exec Project', path: '/tmp' });

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  await expect(page.getByRole('heading', { name: 'New Execution' })).toBeVisible({ timeout: 5000 });

  const form = page.locator('.form-panel');
  await form.getByLabel('Project').selectOption(project.id);

  // Check the agent in pool
  await page.getByRole('checkbox', { name: agent.name }).check();

  await form.getByLabel('Root Agent').selectOption(agent.id);

  await page.getByRole('textbox', { name: 'Task' }).fill('Test with project');
  await page.getByRole('textbox', { name: /title/i }).fill('Project execution');

  await form.getByRole('button', { name: 'Start' }).click();

  await expect(page.getByRole('heading', { name: 'New Execution' })).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Project execution' })).toBeVisible();
});

test('create execution with cwd instead of project', async ({ page }) => {
  const agent = await ensureDirectAgent();

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  await expect(page.getByRole('heading', { name: 'New Execution' })).toBeVisible({ timeout: 5000 });

  // Check the agent in pool
  await page.getByRole('checkbox', { name: agent.name }).check();

  await page.getByLabel('Root Agent').selectOption(agent.id);
  await page.getByRole('textbox', { name: 'Task' }).fill('Test with cwd');
  await page.getByRole('textbox', { name: /title/i }).fill('CWD execution');

  // Advanced section is always visible — fill Working Directory directly
  await page.getByLabel('Working Directory').fill('/tmp');

  await page.getByRole('button', { name: 'Start' }).click();

  await expect(page.getByRole('heading', { name: 'New Execution' })).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'CWD execution' })).toBeVisible();
});
