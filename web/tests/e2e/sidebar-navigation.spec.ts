import { test, expect } from '@playwright/test';
import { apiPost, apiGet, apiDelete, ensureDriver } from './helpers';

const TEST_AGENT_NAMES = ['Deep Link Agent', 'Sidebar Nav Agent'];

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

test('home mode shows dashboard with collapsed sidebar', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.ops-summary, .empty-state').first()).toBeVisible();
  // Sidebar should be collapsed (left panel has collapsed class)
  await expect(page.locator('.left-panel')).toHaveClass(/collapsed/);
});

test('executions icon opens sidebar with execution list', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Executions' }).click();
  await expect(page.locator('.exec-list')).toBeVisible();
});

test('projects icon shows project list in sidebar', async ({ page }) => {
  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  await page.getByRole('button', { name: 'Projects' }).click();
  await expect(page.locator('.project-list')).toBeVisible();
  await expect(page.locator('.exec-list')).not.toBeVisible();
});

test('clicking active icon toggles sidebar collapse', async ({ page }) => {
  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  // Click Executions again — should collapse
  await page.getByRole('button', { name: 'Executions' }).click();
  await expect(page.locator('.left-panel')).toHaveClass(/collapsed/);

  // Click again — should re-open
  await page.getByRole('button', { name: 'Executions' }).click();
  await expect(page.locator('.exec-list')).toBeVisible();
});

test('home icon enters triage mode with collapsed sidebar', async ({ page }) => {
  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  await page.getByRole('button', { name: 'Home' }).click();
  await expect(page.locator('.left-panel')).toHaveClass(/collapsed/);
});

test('deep link to agent detail', async ({ page }) => {
  await cleanupTestAgents();

  const driverId = await ensureDriver('acp');
  const agent = await apiPost('/api/agents', {
    name: 'Deep Link Agent',
    driver_id: driverId,
    config: { command: 'echo', args: [], timeout: 60 },
  });
  createdAgentIds.push(agent.id);

  await page.goto(`/#/agents/${agent.id}`);
  await expect(page.getByRole('heading', { name: 'Deep Link Agent' })).toBeVisible();
  // Sidebar should be open with agent list
  await expect(page.locator('.agent-list')).toBeVisible();
});

test('home mode forces action panel open', async ({ page }) => {
  await page.goto('/');
  const panel = page.getByRole('complementary', { name: 'Decisions panel' });
  // In Home mode, panel should be expanded (showing header text, not just expand button)
  await expect(panel.getByText('DECISIONS')).toBeVisible();
});

test('sidebar state preserved across section switches', async ({ page }) => {
  await page.goto('/#/executions');
  const searchInput = page.locator('.exec-list').getByPlaceholder('Search executions...');
  await expect(searchInput).toBeVisible();

  await searchInput.fill('test-query');

  // Switch to Projects
  await page.getByRole('button', { name: 'Projects' }).click();
  await expect(page.locator('.project-list')).toBeVisible();

  // Switch back to Executions
  await page.getByRole('button', { name: 'Executions' }).click();
  await expect(searchInput).toHaveValue('test-query');
});
