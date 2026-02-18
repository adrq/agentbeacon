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

test('add agent via template', async ({ page }) => {
  await page.goto('/#/agents');

  await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible();

  // Use a quick-add template (or the "Add Agent" button)
  await page.getByRole('button', { name: 'Add Agent' }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Name').fill('E2E Test Agent');
  await dialog.getByLabel('Agent Type').selectOption('acp');
  await dialog.getByRole('button', { name: 'Add' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByText('E2E Test Agent')).toBeVisible();
});

test('edit agent', async ({ page }) => {
  const agent = await apiPost('/api/agents', {
    name: 'Edit Agent',
    agent_type: 'acp',
    config: { command: 'echo', args: [], timeout: 60 },
  });

  await page.goto('/#/agents');
  await expect(page.getByText('Edit Agent')).toBeVisible();

  // Click the edit link on the agent card
  const card = page.locator('.agent-card', { hasText: 'Edit Agent' });
  await card.getByText('Edit').click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Name').clear();
  await dialog.getByLabel('Name').fill('Renamed Agent');
  await dialog.getByRole('button', { name: 'Save' }).click();

  await expect(dialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Renamed Agent')).toBeVisible();
});

test('delete agent', async ({ page }) => {
  const agent = await apiPost('/api/agents', {
    name: 'Delete Agent',
    agent_type: 'acp',
    config: { command: 'echo', args: [], timeout: 60 },
  });

  await page.goto('/#/agents');
  await expect(page.getByText('Delete Agent')).toBeVisible();

  const card = page.locator('.agent-card', { hasText: 'Delete Agent' });
  await card.getByText('Delete').click();

  // AlertDialog confirmation
  const alertDialog = page.getByRole('alertdialog');
  await expect(alertDialog).toBeVisible();
  await alertDialog.getByRole('button', { name: 'Delete' }).click();

  await expect(alertDialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Delete Agent')).not.toBeVisible();
});
