import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureDemoAgent,
  createExecution, waitForWorkerPickup, waitForWorkerIdle,
} from './helpers';

test.beforeEach(async () => {
  await waitForWorkerIdle(30000);
});

test('nav rail renders with navigation and decisions toggle', async ({ page }) => {
  await page.goto('/');

  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(nav).toBeVisible();

  await expect(nav.getByRole('button', { name: 'Home' })).toBeVisible();
  await expect(nav.getByRole('button', { name: 'Executions' })).toBeVisible();
  await expect(nav.getByRole('button', { name: 'Projects' })).toBeVisible();
  await expect(nav.getByRole('button', { name: 'Agents' })).toBeVisible();
  await expect(nav.getByRole('button', { name: 'Toggle decisions panel' })).toBeVisible();
});

test('action panel collapses when no decisions pending', async ({ page }) => {
  // Navigate to executions (not home — home forces ActionPanel open)
  await page.goto('/#/executions');

  const panel = page.getByRole('complementary', { name: 'Decisions panel' });
  await expect(panel).toBeVisible();

  // Should be collapsed (expand button visible, not collapse)
  await expect(panel.getByRole('button', { name: 'Expand decisions panel' })).toBeVisible();
});

test('action panel shows decisions and auto-expands on question arrival', async ({ page }) => {
  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Layout test', 'Layout Q&A');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);

  // Action panel should auto-expand with the decision
  const panel = page.getByRole('complementary', { name: 'Decisions panel' });
  await expect(panel.getByText('DECISIONS (1)')).toBeVisible({ timeout: 20000 });
  await expect(panel.getByText('Which approach should I take?')).toBeVisible();

  // NavRail badge should show count
  const decisionsBtn = page.getByRole('button', { name: 'Toggle decisions panel' });
  await expect(decisionsBtn.getByText('1')).toBeVisible();

  // Answer from the action panel
  await panel.getByRole('radio', { name: /Refactor existing code/ }).click();
  await panel.getByRole('button', { name: /Submit/ }).click();

  // Toast should appear
  await expect(page.getByText('Answer submitted')).toBeVisible({ timeout: 5000 });
});

test('execution search filters the list', async ({ page }) => {
  const agent = await ensureDirectAgent();
  await createExecution(agent.id, 'first task', 'Alpha Search');
  await createExecution(agent.id, 'second task', 'Beta Search');

  // Navigate to executions (not home — home has sidebar collapsed)
  await page.goto('/#/executions');

  // Scope to the sidebar (contains search + execution list)
  const sidebar = page.locator('.sidebar');
  const searchInput = sidebar.getByPlaceholder('Search executions...');
  await expect(searchInput).toBeVisible();

  await searchInput.fill('Alpha');
  await expect(sidebar.getByText('Alpha Search')).toBeVisible();
  await expect(sidebar.getByText('Beta Search')).not.toBeVisible();

  await searchInput.fill('');
  await expect(sidebar.getByText('Alpha Search')).toBeVisible();
  await expect(sidebar.getByText('Beta Search')).toBeVisible();
});

test('action panel stays collapsed at tablet width even with pending decisions', async ({ page }) => {
  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Tablet test', 'Tablet Q&A');
  await waitForWorkerPickup(execId, 15000);

  // Resize to tablet width BEFORE navigating
  await page.setViewportSize({ width: 900, height: 700 });
  await page.goto(`/#/execution/${execId}`);

  const panel = page.getByRole('complementary', { name: 'Decisions panel' });

  // Wait for the question to propagate through polling, then verify panel stays collapsed
  const badge = panel.locator('.collapsed-badge');
  await expect(badge).toBeVisible({ timeout: 20000 });
  await expect(badge).toHaveText(/\d+/);

  // Panel must stay collapsed (showing expand button, not the expanded header)
  await expect(panel.getByRole('button', { name: 'Expand decisions panel' })).toBeVisible();
  await expect(panel.getByText('DECISIONS')).not.toBeVisible();
});

test('elapsed time appears for running executions', async ({ page }) => {
  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Timer test', 'Timer Exec');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);

  // Elapsed time should be visible in the detail header (scope to main-content to avoid sidebar match)
  await expect(page.locator('.main-content .elapsed-time')).toBeVisible({ timeout: 20000 });
});
