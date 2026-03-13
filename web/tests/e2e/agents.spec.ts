import { test, expect } from '@playwright/test';
import { apiPost, apiGet, apiDelete, ensureDriver } from './helpers';

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

test('add agent via sidebar', async ({ page }) => {
  await cleanupTestAgents();

  await page.goto('/#/agents');
  await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible();

  // Click Add Agent in the welcome area (scope to avoid matching sidebar button)
  await page.locator('.agents-welcome').getByRole('button', { name: 'Add Agent' }).click();

  await expect(page.getByRole('heading', { name: 'Add Agent' })).toBeVisible({ timeout: 5000 });

  await page.getByLabel('Name').fill('E2E Test Agent');
  await page.getByLabel('Driver').selectOption({ label: 'acp (ACP)' });
  await page.locator('.form-panel').getByRole('button', { name: 'Add' }).click();

  await expect(page.getByRole('heading', { name: 'Add Agent' })).not.toBeVisible({ timeout: 5000 });
  // Agent should appear in the sidebar list
  await expect(page.locator('.agent-list').getByText('E2E Test Agent')).toBeVisible();
});

test('edit agent via detail view', async ({ page }) => {
  const driverId = await ensureDriver('acp');
  const agent = await apiPost('/api/agents', {
    name: 'Edit Agent',
    driver_id: driverId,
    config: { command: 'echo', args: [], timeout: 60 },
  });
  createdAgentIds.push(agent.id);

  // Navigate directly to agent detail
  await page.goto(`/#/agents/${agent.id}`);
  await expect(page.getByRole('heading', { name: 'Edit Agent' })).toBeVisible();

  await page.locator('.main-content').getByRole('button', { name: 'Edit' }).click();

  await expect(page.locator('.form-panel-title')).toHaveText('Edit Agent', { timeout: 5000 });

  await page.getByLabel('Name').clear();
  await page.getByLabel('Name').fill('Renamed Agent');
  await page.getByRole('button', { name: 'Save' }).click();

  await expect(page.locator('.form-panel-title')).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Renamed Agent' })).toBeVisible();
});

test('delete agent via detail view', async ({ page }) => {
  const driverId = await ensureDriver('acp');
  const agent = await apiPost('/api/agents', {
    name: 'Delete Agent',
    driver_id: driverId,
    config: { command: 'echo', args: [], timeout: 60 },
  });
  createdAgentIds.push(agent.id);

  // Navigate directly to agent detail
  await page.goto(`/#/agents/${agent.id}`);
  await expect(page.getByRole('heading', { name: 'Delete Agent' })).toBeVisible();

  await page.locator('.main-content').getByRole('button', { name: 'Delete' }).click();

  const alertDialog = page.getByRole('alertdialog');
  await expect(alertDialog).toBeVisible();
  await alertDialog.getByRole('button', { name: 'Delete' }).click();

  // Should navigate back to agents welcome
  await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible({ timeout: 5000 });
});
