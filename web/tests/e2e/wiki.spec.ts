import { test, expect, type Page } from '@playwright/test';
import * as fs from 'fs';
import { apiPost, apiGet, apiDelete } from './helpers';

const API_URL = process.env.API_URL ?? 'http://localhost:9456';

interface Project { id: string; name: string }
interface WikiPage { slug: string; title: string; body: string; revision_number: number }

const createdProjectIds: string[] = [];
const createdTempDirs: string[] = [];

async function apiPut(path: string, body: unknown) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API PUT ${path} failed: ${res.status}`);
  return res.json();
}

async function createTestProject(name: string): Promise<Project> {
  const dir = `/tmp/e2e-wiki-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  fs.mkdirSync(dir, { recursive: true });
  createdTempDirs.push(dir);
  const project = await apiPost('/api/projects', { name, path: dir });
  createdProjectIds.push(project.id);
  return project;
}

async function createTestPage(projectId: string, slug: string, title: string, body: string): Promise<WikiPage> {
  return apiPut(`/api/projects/${projectId}/wiki/pages/${slug}`, {
    title,
    body,
    summary: 'test setup',
  });
}

/** Navigate to wiki with clean localStorage — does a full page load so TanStack Query fetches fresh data. */
async function gotoWiki(page: Page, hash = '/#/wiki') {
  await page.addInitScript(() => {
    localStorage.removeItem('agentbeacon-wiki-tabs');
    localStorage.removeItem('agentbeacon-wiki-tabs-active');
  });
  await page.goto(hash);
}

/** Wait for a project option to appear in the select, then select it. */
async function selectProject(page: Page, projectId: string) {
  const combo = page.getByRole('combobox', { name: 'Select project' });
  await expect(combo.locator(`option[value="${projectId}"]`)).toBeAttached({ timeout: 10000 });
  await combo.selectOption(projectId);
}

async function cleanupTestData() {
  for (const id of createdProjectIds) {
    try {
      const pages: { slug: string }[] = await apiGet(`/api/projects/${id}/wiki/pages`);
      for (const p of pages) {
        try { await apiDelete(`/api/projects/${id}/wiki/pages/${p.slug}`); } catch { /* best effort */ }
      }
    } catch { /* best effort */ }
    try { await apiDelete(`/api/projects/${id}`); } catch { /* best effort */ }
  }
  createdProjectIds.length = 0;
  for (const dir of createdTempDirs) {
    try { fs.rmSync(dir, { recursive: true }); } catch { /* best effort */ }
  }
  createdTempDirs.length = 0;
}

test.afterEach(async () => {
  await cleanupTestData();
});

test('wiki navrail shows section with collapsed sidebar', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Wiki').click();
  await expect(page.getByRole('tablist').first()).toBeVisible();
});

test('wiki search tab default state', async ({ page }) => {
  await gotoWiki(page);
  await expect(page.getByRole('tab', { name: /Search/ }).first()).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'Select project' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Search wiki pages' })).toBeVisible();
});

test('wiki search lists pages for selected project', async ({ page }) => {
  const project = await createTestProject('Wiki Search Test');
  await createTestPage(project.id, 'page-one', 'Page One', '# Page One\nContent here.');
  await createTestPage(project.id, 'page-two', 'Page Two', '# Page Two\nMore content.');

  await gotoWiki(page);
  await selectProject(page, project.id);

  await expect(page.getByRole('button', { name: /Page One/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Page Two/ })).toBeVisible();
});

test('wiki search filters results', async ({ page }) => {
  const project = await createTestProject('Wiki Filter Test');
  await createTestPage(project.id, 'architecture', 'Architecture Overview', '# Architecture\nSystem architecture docs.');
  await createTestPage(project.id, 'deployment', 'Deployment Guide', '# Deployment\nHow to deploy.');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Architecture/ })).toBeVisible();

  await page.getByRole('textbox', { name: 'Search wiki pages' }).fill('architecture');

  await expect(page.getByRole('button', { name: /Architecture/ })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('button', { name: /Deployment/ })).not.toBeVisible({ timeout: 3000 });
});

test('wiki open page tab with rendered markdown', async ({ page }) => {
  const project = await createTestProject('Wiki Page View Test');
  await createTestPage(project.id, 'readme', 'Project Readme', '# Welcome\n\nThis is a **test** page.');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Project Readme/ })).toBeVisible();
  await page.getByRole('button', { name: /Project Readme/ }).click();

  await expect(page.getByRole('tab', { name: /Project Readme/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Welcome' })).toBeVisible();
  await expect(page.locator('strong').filter({ hasText: 'test' })).toBeVisible();
});

test('wiki tab deduplication', async ({ page }) => {
  const project = await createTestProject('Wiki Dedup Test');
  await createTestPage(project.id, 'unique-page', 'Unique Page', '# Unique');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Unique Page/ })).toBeVisible();

  await page.getByRole('button', { name: /Unique Page/ }).click();
  await expect(page.getByRole('tab', { name: /Unique Page/ })).toBeVisible();
  const tabsBefore = await page.getByRole('tab').count();

  await page.getByRole('tab', { name: /Search/ }).first().click();
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Unique Page/ })).toBeVisible();
  await page.getByRole('button', { name: /Unique Page/ }).click();

  const tabsAfter = await page.getByRole('tab').count();
  expect(tabsAfter).toBe(tabsBefore);
});

test('wiki multi-project tabs coexist', async ({ page }) => {
  const projectA = await createTestProject('Wiki Multi A');
  const projectB = await createTestProject('Wiki Multi B');
  await createTestPage(projectA.id, 'page-a', 'Page From A', '# A content');
  await createTestPage(projectB.id, 'page-b', 'Page From B', '# B content');

  await gotoWiki(page);

  await selectProject(page, projectA.id);
  await expect(page.getByRole('button', { name: /Page From A/ })).toBeVisible();
  await page.getByRole('button', { name: /Page From A/ }).click();
  await expect(page.getByRole('heading', { name: 'A content' })).toBeVisible();

  await page.getByRole('button', { name: 'New search tab' }).click();

  await selectProject(page, projectB.id);
  await expect(page.getByRole('button', { name: /Page From B/ })).toBeVisible();
  await page.getByRole('button', { name: /Page From B/ }).click();
  await expect(page.getByRole('heading', { name: 'B content' })).toBeVisible();

  await expect(page.getByRole('tab', { name: /Page From A/ })).toBeVisible();
  await expect(page.getByRole('tab', { name: /Page From B/ })).toBeVisible();
});

test('wiki edit and preview toggle', async ({ page }) => {
  const project = await createTestProject('Wiki Edit Test');
  await createTestPage(project.id, 'editable', 'Editable Page', '# Editable\n\nOriginal content.');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Editable Page/ })).toBeVisible();
  await page.getByRole('button', { name: /Editable Page/ }).click();

  await page.getByRole('button', { name: 'Edit' }).click();
  await expect(page.getByRole('tab', { name: 'Edit', exact: true })).toBeVisible();
  await expect(page.locator('textarea')).toBeVisible();

  await page.getByRole('tab', { name: 'Preview' }).click();
  await expect(page.getByRole('heading', { name: 'Editable', exact: true })).toBeVisible();
});

test('wiki save page updates content', async ({ page }) => {
  const project = await createTestProject('Wiki Save Test');
  await createTestPage(project.id, 'save-test', 'Save Test', '# Before\n\nOld content.');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Save Test/ })).toBeVisible();
  await page.getByRole('button', { name: /Save Test/ }).click();

  await page.getByRole('button', { name: 'Edit' }).click();
  await page.locator('textarea').fill('# After\n\nNew content here.');
  await page.getByRole('textbox', { name: 'Edit summary' }).fill('Updated content');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.getByRole('heading', { name: 'After' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('New content here.')).toBeVisible();
});

test('wiki history shows revisions', async ({ page }) => {
  const project = await createTestProject('Wiki History Test');
  await createTestPage(project.id, 'history-page', 'History Page', '# Version 1');
  await apiPut(`/api/projects/${project.id}/wiki/pages/history-page`, {
    title: 'History Page',
    body: '# Version 2',
    revision_number: 1,
    summary: 'Second revision',
  });
  await apiPut(`/api/projects/${project.id}/wiki/pages/history-page`, {
    title: 'History Page',
    body: '# Version 3',
    revision_number: 2,
    summary: 'Third revision',
  });

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /History Page/ })).toBeVisible();
  await page.getByRole('button', { name: /History Page/ }).click();

  await page.getByRole('button', { name: 'History' }).click();
  // Revisions API returns historical revisions (not the current one).
  // With 3 total revisions, Rev 1 and Rev 2 appear in history.
  await expect(page.getByRole('button', { name: /Rev 2/ })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('button', { name: /Rev 1/ })).toBeVisible();
});

test('wiki close tab removes it and search tab is unclosable', async ({ page }) => {
  const project = await createTestProject('Wiki Close Test');
  await createTestPage(project.id, 'closeme', 'Close Me', '# Close');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Close Me/ })).toBeVisible();
  await page.getByRole('button', { name: /Close Me/ }).click();

  await expect(page.getByRole('tab', { name: /Close Me/ })).toBeVisible();
  // Close button is a sibling of the tab trigger inside .tab-wrapper
  const closeBtn = page.getByRole('tab', { name: /Close Me/ }).locator('..').getByRole('button', { name: 'Close tab' });
  await closeBtn.click();

  await expect(page.getByRole('tab', { name: /Close Me/ })).not.toBeVisible();
  await expect(page.getByRole('tab', { name: /Search/ }).first()).toBeVisible();
});

test('wiki new tab button opens search tab', async ({ page }) => {
  await gotoWiki(page);
  const tabsBefore = await page.getByRole('tab').count();
  await page.getByRole('button', { name: 'New search tab' }).click();
  const tabsAfter = await page.getByRole('tab').count();
  expect(tabsAfter).toBe(tabsBefore + 1);
});

test('wiki tab persistence across reload', async ({ page }) => {
  const project = await createTestProject('Wiki Persist Test');
  await createTestPage(project.id, 'persist-page', 'Persist Page', '# Persistent');

  // First load: open a page tab (don't clear localStorage via addInitScript here)
  await page.goto('/#/wiki');
  await page.evaluate(() => {
    localStorage.removeItem('agentbeacon-wiki-tabs');
    localStorage.removeItem('agentbeacon-wiki-tabs-active');
  });
  await page.reload();
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Persist Page/ })).toBeVisible();
  await page.getByRole('button', { name: /Persist Page/ }).click();
  await expect(page.getByRole('tab', { name: /Persist Page/ })).toBeVisible();

  // Reload — tab should survive (no addInitScript clearing localStorage)
  await page.reload();
  await expect(page.getByRole('tab', { name: /Persist Page/ })).toBeVisible({ timeout: 5000 });
});

test('wiki deep link opens page', async ({ page }) => {
  const project = await createTestProject('Wiki Deep Link Test');
  await createTestPage(project.id, 'deep-target', 'Deep Target', '# Deep Linked');

  await gotoWiki(page, `/#/wiki/${project.id}/deep-target`);

  await expect(page.getByRole('tab', { name: /Deep Target|deep-target/ })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Deep Linked' })).toBeVisible();
});

test('wiki delete page with confirmation', async ({ page }) => {
  const project = await createTestProject('Wiki Delete Test');
  await createTestPage(project.id, 'delete-target', 'Delete Target', '# Delete me');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByRole('button', { name: /Delete Target/ })).toBeVisible();
  await page.getByRole('button', { name: /Delete Target/ }).click();

  await page.getByRole('button', { name: 'Delete' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Delete' }).click();

  await expect(page.getByRole('tab', { name: /Delete Target/ })).not.toBeVisible({ timeout: 5000 });
});

test('wiki create new page', async ({ page }) => {
  const project = await createTestProject('Wiki Create Test');

  await gotoWiki(page);
  await selectProject(page, project.id);
  await expect(page.getByText(/No wiki pages/)).toBeVisible({ timeout: 5000 });

  await page.getByRole('button', { name: 'Create new page' }).click();
  await page.getByRole('textbox', { name: 'New page slug' }).fill('brand-new-page');
  await page.getByRole('button', { name: 'Create' }).click();

  await expect(page.getByRole('heading', { name: /New Page/ })).toBeVisible({ timeout: 5000 });
  await page.getByRole('textbox', { name: 'Title' }).fill('Brand New Page');
  await page.locator('textarea').fill('# Brand New\n\nFresh content.');
  await page.getByRole('button', { name: 'Create' }).click();

  await expect(page.getByRole('heading', { name: 'Brand New', exact: true })).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Fresh content.')).toBeVisible();
});

test('wiki cross-section link from project detail', async ({ page }) => {
  const project = await createTestProject('Wiki Cross Link Test');
  await createTestPage(project.id, 'linked-page', 'Linked Page', '# Linked');

  await page.goto(`/#/projects/${project.id}`);
  await expect(page.getByRole('heading', { name: 'Wiki Cross Link Test' })).toBeVisible();

  // Scope to header actions to avoid matching the NavRail Wiki button
  await page.locator('.header-actions').getByRole('button', { name: 'Wiki' }).click();

  await expect(page.getByRole('combobox', { name: 'Select project' })).toHaveValue(project.id, { timeout: 10000 });
  await expect(page.getByRole('button', { name: /Linked Page/ })).toBeVisible();
});
