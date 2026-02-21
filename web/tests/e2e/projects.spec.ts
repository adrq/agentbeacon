import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { apiPost, apiGet, apiDelete } from './helpers';

const TEST_PROJECT_NAMES = ['E2E Test Project', 'Detail Test', 'Edit Me', 'Edited Project', 'Delete Me'];

const createdProjectIds: string[] = [];
const createdTempDirs: string[] = [];

async function cleanupTestProjects() {
  for (const id of createdProjectIds) {
    try { await apiDelete(`/api/projects/${id}`); } catch { /* best effort */ }
  }
  createdProjectIds.length = 0;

  const projects: { id: string; name: string }[] = await apiGet('/api/projects');
  for (const p of projects) {
    if (TEST_PROJECT_NAMES.includes(p.name)) {
      try { await apiDelete(`/api/projects/${p.id}`); } catch { /* best effort */ }
    }
  }

  for (const dir of createdTempDirs) {
    try { fs.rmSync(dir, { recursive: true }); } catch { /* best effort */ }
  }
  createdTempDirs.length = 0;
}

test.afterEach(async () => {
  await cleanupTestProjects();
});

test('register a project via UI', async ({ page }) => {
  await cleanupTestProjects();

  // Unique path that actually exists on disk — avoids both:
  // (a) "another project uses this path" warning (keeps dialog open)
  // (b) "path does not exist" validation error (backend canonicalize fails)
  const uniquePath = `/tmp/e2e-project-${Date.now()}`;
  fs.mkdirSync(uniquePath, { recursive: true });
  createdTempDirs.push(uniquePath);

  await page.goto('/#/projects');

  await page.getByRole('button', { name: 'Register Project' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Name').fill('E2E Test Project');
  await dialog.getByLabel('Path').fill(uniquePath);
  await dialog.getByRole('button', { name: 'Register' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.locator('.project-card', { hasText: 'E2E Test Project' })).toBeVisible();
});

test('navigate to project detail', async ({ page }) => {
  const project = await apiPost('/api/projects', { name: 'Detail Test', path: '/tmp' });
  createdProjectIds.push(project.id);

  await page.goto('/#/projects');
  await page.locator('.project-card', { hasText: 'Detail Test' }).first().click();

  await expect(page.getByRole('heading', { name: 'Detail Test' })).toBeVisible();
  await expect(page.getByText('/tmp')).toBeVisible();
});

test('edit project', async ({ page }) => {
  const project = await apiPost('/api/projects', { name: 'Edit Me', path: '/tmp' });
  createdProjectIds.push(project.id);

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
  createdProjectIds.push(project.id);

  await page.goto(`/#/projects/${project.id}`);
  await expect(page.getByRole('heading', { name: 'Delete Me' })).toBeVisible();

  await page.getByRole('button', { name: 'Delete' }).click();

  const alertDialog = page.getByRole('alertdialog');
  await expect(alertDialog).toBeVisible();
  await alertDialog.getByRole('button', { name: 'Delete' }).click();

  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible({ timeout: 5000 });
});
