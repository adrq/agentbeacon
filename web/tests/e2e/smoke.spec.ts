import { test, expect } from '@playwright/test';
import {
  apiPost, apiGet, ensureDirectAgent, ensureDemoAgent, ensureTCLeadAgent,
  ensureTCChildAgent, createExecution, waitForWorkerPickup, waitForEvent,
  waitForWorkerIdle,
} from './helpers';

async function ensureProject(): Promise<{ id: string; name: string }> {
  const projects: { id: string; name: string }[] = await apiGet('/api/projects');
  if (projects.length > 0) return projects[0];
  const result = await apiPost('/api/projects', { name: 'smoke-test', path: '/tmp' });
  return { id: result.id, name: result.name };
}

test('app loads with header and new button', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByText('AgentBeacon')).toBeVisible();
  await expect(page.getByRole('button', { name: '+ New' })).toBeVisible();
});

test('theme toggle switches between light and dark', async ({ page }) => {
  await page.goto('/');

  const toggle = page.getByRole('button', { name: /Activate .+ theme/ });
  await expect(toggle).toBeVisible();

  await toggle.click();
  await expect(page.getByRole('button', { name: /Activate .+ theme/ })).toBeVisible();
});

test('create execution via modal', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const project = await ensureProject();

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  const dialog = page.getByRole('dialog', { name: 'New Execution' });
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Project', { exact: true }).selectOption(project.id);
  await dialog.getByLabel('Agent').selectOption(agent.id);

  await page.getByRole('textbox', { name: 'Task' }).fill('Playwright smoke test task');
  await page.getByRole('textbox', { name: /title/i }).fill('Smoke test');

  await page.getByRole('button', { name: 'Start' }).click();

  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Smoke test' })).toBeVisible();
  // Worker may pick up the execution before this assertion fires, so accept any active status
  await expect(page.getByText(/Submitted|Working|Turn Complete|Awaiting Input/)).toBeVisible();
});

test('full question-answer flow', async ({ page }) => {
  await waitForWorkerIdle();

  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Smoke Q&A test', 'Q&A flow test');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);

  // Question appears in the QuestionBanner within the execution detail
  const banner = page.locator('.question-banner');
  await expect(banner).toBeVisible({ timeout: 20000 });

  // Scope radio/submit to the banner to avoid matching the ActionPanel's DecisionCard
  await expect(banner.getByRole('radio', { name: /Refactor existing code/ })).toBeVisible();
  await expect(banner.getByRole('radio', { name: /Write new module/ })).toBeVisible();
  await expect(banner.getByRole('radio', { name: /Decide for me/ })).toBeVisible();

  await expect(banner.getByRole('button', { name: /Submit/ })).toBeDisabled();

  await banner.getByRole('radio', { name: /Refactor existing code/ }).click();
  await expect(banner.getByRole('button', { name: /Submit/ })).toBeEnabled();

  await banner.getByRole('button', { name: /Submit/ }).click();

  await expect(
    page.locator('.timeline-entry').filter({ hasText: 'User: Refactor existing code' })
  ).toBeVisible({ timeout: 10000 });
});

test('turn-complete delivers child output to parent', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();

  const { execId } = await createExecution(lead.id, 'Turn-complete E2E test', 'TC round-trip', [child.id]);
  await waitForWorkerPickup(execId, 15000);

  // Wait for turn-complete event to be recorded before navigating to UI
  await waitForEvent(execId, 'turn_complete', 30000);

  // Navigate to execution and verify turn_complete renders
  await page.goto(`/#/execution/${execId}`);

  const tcEntry = page.locator('.timeline-entry').filter({ hasText: 'Child reported' });
  await expect(tcEntry).toBeVisible({ timeout: 10000 });
  await expect(tcEntry).toContainText('END_TURN_PHASE_0');

  // Switch to chat view and verify rendering there too
  await page.getByRole('tab', { name: 'Chat' }).click();
  const chatEntry = page.locator('.tool-card').filter({ hasText: 'Child reported' });
  await expect(chatEntry).toBeVisible({ timeout: 5000 });
});

test('navigation between views', async ({ page }) => {
  await page.goto('/');

  // Click Projects — sidebar swaps to ProjectList, main shows ProjectsWelcome
  await page.getByRole('button', { name: 'Projects' }).click();
  await expect(page.locator('.project-list')).toBeVisible();
  await expect(page.getByText('Register Project')).toBeVisible();

  // Click Agents — sidebar swaps to AgentList, main shows AgentsWelcome
  await page.getByRole('button', { name: 'Agents' }).click();
  await expect(page.locator('.agent-list')).toBeVisible();
  await expect(page.getByText('Add Agent')).toBeVisible();

  // Click Executions — sidebar swaps to ExecutionList
  await page.getByRole('button', { name: 'Executions' }).click();
  await expect(page.locator('.exec-list')).toBeVisible();
});
