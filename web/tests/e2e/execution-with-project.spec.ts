import { test, expect } from '@playwright/test';

const API_URL = process.env.API_URL ?? 'http://localhost:9456';

async function apiPost(path: string, body: unknown) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

async function apiGet(path: string) {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

async function ensureAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string }[] = await apiGet('/api/agents');
  if (agents.length > 0) return agents[0];
  throw new Error('No agents seeded');
}

test('create execution with project selected', async ({ page }) => {
  const agent = await ensureAgent();
  const project = await apiPost('/api/projects', { name: 'Exec Project', path: '/tmp' });

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  // Select project
  await dialog.getByLabel('Project').selectOption(project.id);

  // Select agent
  await dialog.getByLabel('Agent').selectOption(agent.id);

  await dialog.getByRole('textbox', { name: 'Task' }).fill('Test with project');
  await dialog.getByRole('textbox', { name: /title/i }).fill('Project execution');

  await dialog.getByRole('button', { name: 'Start' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Project execution' })).toBeVisible();
});

test('create execution with cwd instead of project', async ({ page }) => {
  const agent = await ensureAgent();

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Agent').selectOption(agent.id);
  await dialog.getByRole('textbox', { name: 'Task' }).fill('Test with cwd');
  await dialog.getByRole('textbox', { name: /title/i }).fill('CWD execution');

  // Open advanced and set cwd
  await dialog.getByText('Show Advanced').click();
  await dialog.getByLabel('Working Directory').fill('/tmp');

  await dialog.getByRole('button', { name: 'Start' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'CWD execution' })).toBeVisible();
});
