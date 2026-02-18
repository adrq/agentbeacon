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

async function apiDelete(path: string) {
  const res = await fetch(`${API_URL}${path}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`API ${path} failed: ${res.status}`);
}

async function apiGet(path: string) {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

test('register a project via UI', async ({ page }) => {
  await page.goto('/#/projects');

  await page.getByRole('button', { name: 'Register Project' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Name').fill('E2E Test Project');
  await dialog.getByLabel('Path').fill('/tmp');
  await dialog.getByRole('button', { name: 'Register' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByText('E2E Test Project')).toBeVisible();
});

test('navigate to project detail', async ({ page }) => {
  const project = await apiPost('/api/projects', { name: 'Detail Test', path: '/tmp' });

  await page.goto('/#/projects');
  await page.getByText('Detail Test').click();

  await expect(page.getByRole('heading', { name: 'Detail Test' })).toBeVisible();
  await expect(page.getByText('/tmp')).toBeVisible();
});

test('edit project', async ({ page }) => {
  const project = await apiPost('/api/projects', { name: 'Edit Me', path: '/tmp' });

  await page.goto(`/#/projects/${project.id}`);
  await expect(page.getByRole('heading', { name: 'Edit Me' })).toBeVisible();

  await page.getByRole('button', { name: 'Edit' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Name').clear();
  await dialog.getByLabel('Name').fill('Edited Project');
  await dialog.getByRole('button', { name: 'Save' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Edited Project' })).toBeVisible();
});

test('delete project', async ({ page }) => {
  const project = await apiPost('/api/projects', { name: 'Delete Me', path: '/tmp' });

  await page.goto(`/#/projects/${project.id}`);
  await expect(page.getByRole('heading', { name: 'Delete Me' })).toBeVisible();

  await page.getByRole('button', { name: 'Delete' }).click();

  // AlertDialog confirmation
  const alertDialog = page.getByRole('alertdialog');
  await expect(alertDialog).toBeVisible();
  await alertDialog.getByRole('button', { name: 'Delete' }).click();

  // Should navigate back to projects list
  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible({ timeout: 5000 });
});
