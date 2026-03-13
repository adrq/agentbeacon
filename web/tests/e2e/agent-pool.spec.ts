import { test, expect } from '@playwright/test';
import {
  apiPost, apiGet, apiDelete, ensureDirectAgent, ensureDemoAgent,
  createExecution, waitForTurnEnd, waitForTerminal, waitForWorkerIdle,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('execution creation with multi-agent pool', async ({ page }) => {
  const agent1 = await ensureDirectAgent();
  const agent2 = await ensureDemoAgent();

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  await expect(page.getByRole('heading', { name: 'New Execution' })).toBeVisible({ timeout: 5000 });

  // Check both agents in pool
  await page.getByRole('checkbox', { name: agent1.name }).check();
  await page.getByRole('checkbox', { name: agent2.name }).check();

  // Root Agent dropdown should contain both agents
  const rootSelect = page.getByRole('combobox', { name: 'Root Agent' });
  await rootSelect.selectOption({ label: agent1.name });

  // Fill form — advanced section is always visible
  await page.getByRole('textbox', { name: 'Task' }).fill('Multi-agent pool test');
  await page.getByRole('textbox', { name: /title/i }).fill('Pool E2E');

  await page.getByLabel('Working Directory').fill('/tmp');

  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page.getByRole('heading', { name: 'New Execution' })).not.toBeVisible({ timeout: 5000 });

  // Verify navigated to execution detail
  await expect(page.getByRole('heading', { name: 'Pool E2E' })).toBeVisible({ timeout: 10000 });

  // Verify pool section shows both agents
  const poolSection = page.locator('.pool-section');
  await expect(poolSection).toBeVisible({ timeout: 5000 });
});

test('project agent pool management via UI', async ({ page }) => {
  const agent = await ensureDirectAgent();

  // Create a test project
  const project = await apiPost('/api/projects', { name: 'Pool Mgmt Test', path: '/tmp' });
  const projectId = project.id;

  try {
    await page.goto(`/#/projects/${projectId}`);
    await expect(page.getByRole('heading', { name: 'Pool Mgmt Test' })).toBeVisible({ timeout: 10000 });

    // Pool section should exist
    const poolHeading = page.getByRole('heading', { name: 'Agent Pool' });
    await expect(poolHeading).toBeVisible();

    // Backend defaults new projects to all agents — find our agent's pool tag
    const poolTag = page.locator('.pool-tag', { hasText: agent.name });
    await expect(poolTag).toBeVisible({ timeout: 5000 });

    // Remove agent from pool
    await poolTag.locator('.pool-tag-remove').click();
    await expect(poolTag).not.toBeVisible({ timeout: 5000 });

    // Re-add agent to pool
    await page.getByRole('button', { name: '+ Add Agent' }).click();
    const addSelect = page.locator('.pool-add-select');
    await expect(addSelect).toBeVisible();
    await addSelect.selectOption({ label: agent.name });

    // Verify agent reappears as pool tag
    await expect(poolTag).toBeVisible({ timeout: 5000 });
  } finally {
    await apiDelete(`/api/projects/${projectId}`);
  }
});

test('re-run preserves agent pool', async ({ page }) => {
  const agent = await ensureDirectAgent();
  // Use EXIT_1 to reach terminal (failed) state naturally
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'Rerun Pool Test');
  await waitForTerminal(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  const rerunBtn = header.getByRole('button', { name: 'Re-run' });
  await expect(rerunBtn).toBeVisible({ timeout: 10000 });

  await rerunBtn.click();

  await expect(page.getByRole('heading', { name: 'Re-run Execution' })).toBeVisible({ timeout: 5000 });

  // Verify task is pre-filled
  const taskInput = page.getByRole('textbox', { name: 'Task' });
  await expect(taskInput).toHaveValue('EXIT_1');

  // Verify title is pre-filled
  const titleInput = page.getByRole('textbox', { name: /title/i });
  await expect(titleInput).toHaveValue('Re-run: Rerun Pool Test');

  // Verify pool section shows selected agents
  await expect(page.getByText(/Agent Pool.*\d+ selected/)).toBeVisible();

  // Navigate away from form
  await page.locator('.form-panel').getByRole('button', { name: 'Cancel' }).click();
});
