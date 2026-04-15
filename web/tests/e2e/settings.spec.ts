import { test, expect } from '@playwright/test';
import { apiGet, apiPost, ensureDriver } from './helpers';

test('settings gear visible in NavRail (not in header)', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('AgentBeacon')).toBeVisible();

  // Gear icon should be in the nav rail
  const navRail = page.locator('.nav-rail');
  await expect(navRail.getByRole('button', { name: 'Settings' })).toBeVisible();

  // Gear icon should NOT be in the header
  const header = page.locator('.app-header');
  await expect(header.getByRole('button', { name: 'Settings' })).not.toBeAttached();
});

test('settings gear navigates to settings page', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('AgentBeacon')).toBeVisible();

  // Click gear icon in NavRail
  await page.locator('.nav-rail').getByRole('button', { name: 'Settings' }).click();

  // Verify settings page renders
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Verify URL changed
  expect(page.url()).toContain('#/settings');
});

test('settings gear has active state when on settings page', async ({ page }) => {
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Settings nav-rail item should be active
  const settingsBtn = page.locator('.nav-rail').getByRole('button', { name: 'Settings' });
  await expect(settingsBtn).toHaveClass(/active/);
});

test('Integrations section visible on settings page', async ({ page }) => {
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible();
});

test('MCP servers section renders in Integrations', async ({ page }) => {
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // MCP servers section should be inside the integrations section
  const integrationsSection = page.locator('#integrations');
  await expect(integrationsSection).toBeVisible();

  // The MCP section header or empty state should be visible
  const mcpSection = integrationsSection.locator('.mcp-section');
  await expect(mcpSection).toBeVisible();
});

test('agent system_prompt field in form', async ({ page }) => {
  const agents: { id: string; name: string }[] = await apiGet('/api/agents');
  let agent = agents.find(a => a.name === 'System Prompt Test Agent');
  if (!agent) {
    const driverId = await ensureDriver('acp');
    agent = await apiPost('/api/agents', {
      name: 'System Prompt Test Agent',
      driver_id: driverId,
      description: 'Agent for testing system_prompt form field',
      config: { command: 'echo', args: ['noop'], timeout: 60 },
    });
  }

  await page.goto(`/#/agents/${agent!.id}`);
  await expect(page.getByRole('heading', { name: agent!.name })).toBeVisible({ timeout: 10000 });

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
