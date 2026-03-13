import { test, expect } from '@playwright/test';
import { apiGet, apiPost, ensureDirectAgent, waitForWorkerIdle } from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('settings page accessible via gear icon', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('AgentBeacon')).toBeVisible();

  // Click gear icon
  await page.getByRole('button', { name: 'Settings' }).click();

  // Verify settings page renders
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Verify URL changed
  expect(page.url()).toContain('#/settings');

  // No sidebar should be visible (settings has no sidebar)
  await expect(page.locator('.nav-rail-item.active')).not.toBeAttached();
});

test('config entries displayed and editable', async ({ page }) => {
  // Ensure at least one config entry exists
  const configs: { name: string; value: string }[] = await apiGet('/api/config');
  if (configs.length === 0) {
    await apiPost('/api/config', { name: 'test_key', value: 'test_value' });
  }

  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Verify at least one config section renders
  const configSections = page.locator('.settings-entry');
  const count = await configSections.count();
  expect(count).toBeGreaterThan(0);

  // Each section should have a textarea and Save button
  const firstSection = configSections.first();
  await expect(firstSection.locator('textarea')).toBeVisible();
  await expect(firstSection.getByRole('button', { name: 'Save' })).toBeVisible();

  // Save should be disabled when value hasn't changed
  await expect(firstSection.getByRole('button', { name: 'Save' })).toBeDisabled();

  // Modify the value
  const textarea = firstSection.locator('textarea');
  const originalValue = await textarea.inputValue();
  await textarea.fill(originalValue + ' modified');

  // Save should now be enabled
  await expect(firstSection.getByRole('button', { name: 'Save' })).toBeEnabled();

  // Click save
  await firstSection.getByRole('button', { name: 'Save' }).click();

  // Should show success feedback
  await expect(firstSection.getByText('Saved')).toBeVisible({ timeout: 5000 });

  // Restore original value
  await textarea.fill(originalValue);
  await firstSection.getByRole('button', { name: 'Save' }).click();
  await expect(firstSection.getByText('Saved')).toBeVisible({ timeout: 5000 });
});

test('agent system_prompt field in form', async ({ page }) => {
  const agent = await ensureDirectAgent();

  await page.goto(`/#/agents/${agent.id}`);
  await expect(page.getByRole('heading', { name: agent.name })).toBeVisible({ timeout: 10000 });

  // Click Edit
  await page.getByRole('button', { name: 'Edit' }).click();
  await expect(page.locator('.form-panel-title')).toHaveText('Edit Agent', { timeout: 5000 });

  // Verify System Prompt field exists
  const systemPromptField = page.getByLabel('System Prompt');
  await expect(systemPromptField).toBeVisible();

  // Fill in a system prompt
  await systemPromptField.fill('You are a helpful test agent.');

  // Save
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('.form-panel-title')).not.toBeVisible({ timeout: 5000 });

  // Verify system_prompt is displayed in detail view
  await expect(page.getByText('You are a helpful test agent.')).toBeVisible({ timeout: 5000 });

  // Edit again and clear system prompt to clean up
  await page.getByRole('button', { name: 'Edit' }).click();
  await expect(page.locator('.form-panel-title')).toHaveText('Edit Agent', { timeout: 5000 });
  await page.getByLabel('System Prompt').fill('');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('.form-panel-title')).not.toBeVisible({ timeout: 5000 });
});
