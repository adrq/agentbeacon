import { test, expect } from '@playwright/test';
import { apiPost, apiGet, apiDelete } from './helpers';

const TEST_AGENT_NAMES = ['E2E Test Agent', 'Edit Agent', 'Renamed Agent', 'Delete Agent'];

const createdAgentIds: string[] = [];

async function cleanupTestAgents() {
  for (const id of createdAgentIds) {
    try { await apiDelete(`/api/agents/${id}`); } catch { /* best effort */ }
  }
  createdAgentIds.length = 0;

  const agents: { id: string; name: string }[] = await apiGet('/api/agents');
  for (const agent of agents) {
    if (TEST_AGENT_NAMES.includes(agent.name)) {
      try { await apiDelete(`/api/agents/${agent.id}`); } catch { /* best effort */ }
    }
  }
}

test.afterEach(async () => {
  await cleanupTestAgents();
});

test('add agent via template', async ({ page }) => {
  await cleanupTestAgents();

  await page.goto('/#/agents');
  await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible();

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
  createdAgentIds.push(agent.id);

  await page.goto('/#/agents');
  await expect(page.getByText('Edit Agent')).toBeVisible();

  const card = page.locator('.agent-card', { hasText: 'Edit Agent' });
  await card.getByRole('button', { name: 'Edit' }).click();

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
  createdAgentIds.push(agent.id);

  await page.goto('/#/agents');
  await expect(page.getByText('Delete Agent')).toBeVisible();

  const card = page.locator('.agent-card', { hasText: 'Delete Agent' });
  await card.getByRole('button', { name: 'Delete' }).click();

  const alertDialog = page.getByRole('alertdialog');
  await expect(alertDialog).toBeVisible();
  await alertDialog.getByRole('button', { name: 'Delete' }).click();

  await expect(alertDialog).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Delete Agent')).not.toBeVisible();
});
