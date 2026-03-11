import { test, expect } from '@playwright/test';
import { apiPost, ensureDirectAgent } from './helpers';

test('create execution with project selected', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const project = await apiPost('/api/projects', { name: 'Exec Project', path: '/tmp' });

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Project').selectOption(project.id);

  // Expand pool, check the agent, then collapse so Start stays in viewport
  await dialog.getByRole('button', { name: /Agent Pool/ }).click();
  await dialog.getByRole('checkbox', { name: agent.name }).check();
  await dialog.getByRole('button', { name: /Agent Pool/ }).click();

  await dialog.getByLabel('Root Agent').selectOption(agent.id);

  await dialog.getByRole('textbox', { name: 'Task' }).fill('Test with project');
  await dialog.getByRole('textbox', { name: /title/i }).fill('Project execution');

  await dialog.getByRole('button', { name: 'Start' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Project execution' })).toBeVisible();
});

test('create execution with cwd instead of project', async ({ page }) => {
  const agent = await ensureDirectAgent();

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  // Expand pool, check the agent, then collapse so Start stays in viewport
  await dialog.getByRole('button', { name: /Agent Pool/ }).click();
  await dialog.getByRole('checkbox', { name: agent.name }).check();
  await dialog.getByRole('button', { name: /Agent Pool/ }).click();

  await dialog.getByLabel('Root Agent').selectOption(agent.id);
  await dialog.getByRole('textbox', { name: 'Task' }).fill('Test with cwd');
  await dialog.getByRole('textbox', { name: /title/i }).fill('CWD execution');

  await dialog.getByText('Show Advanced').click();
  await dialog.getByLabel('Working Directory').fill('/tmp');

  await dialog.getByRole('button', { name: 'Start' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'CWD execution' })).toBeVisible();
});
