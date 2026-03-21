import { test, expect } from '@playwright/test';
import { apiGet, apiPost, ensureDirectAgent, waitForWorkerIdle } from './helpers';

// Track config keys created by tests so we can clean them up.
// There is no DELETE endpoint for config; we zero out the value instead.
const testCreatedConfigKeys = new Set<string>();

async function cleanupTestConfig() {
  for (const key of testCreatedConfigKeys) {
    try { await apiPost('/api/config', { name: key, value: '' }); } catch { /* best effort */ }
  }
  testCreatedConfigKeys.clear();
}

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterAll(async () => {
  await cleanupTestConfig();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

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

test('settings sidebar visible with section links', async ({ page }) => {
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Settings sidebar should be visible
  const sidebar = page.locator('.settings-sidebar');
  await expect(sidebar).toBeVisible();

  // Sidebar should contain section links
  await expect(sidebar.getByRole('button', { name: 'Briefing Templates' })).toBeVisible();
  await expect(sidebar.getByRole('button', { name: 'Integrations' })).toBeVisible();
});

test('clicking sidebar items scrolls to sections', async ({ page }) => {
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  const sidebar = page.locator('.settings-sidebar');
  const content = page.locator('.settings-content');

  // Only assert scroll positions if the content pane is actually scrollable.
  const isScrollable = await content.evaluate(
    el => el.scrollHeight > el.clientHeight,
  );
  if (!isScrollable) {
    // Content is too short to scroll — just verify the buttons are clickable.
    await sidebar.getByRole('button', { name: 'Integrations' }).click();
    await sidebar.getByRole('button', { name: 'Briefing Templates' }).click();
    return;
  }

  // Click Integrations — wait for scroll to settle (smooth scroll may take a moment)
  await sidebar.getByRole('button', { name: 'Integrations' }).click();
  let scrollAfterIntegrations = 0;
  await expect(async () => {
    const a = await content.evaluate(el => el.scrollTop);
    await page.waitForTimeout(80);
    const b = await content.evaluate(el => el.scrollTop);
    // Stable and > 0 (scroll animation has finished)
    expect(b).toBeGreaterThan(0);
    expect(b).toBe(a);
    scrollAfterIntegrations = b;
  }).toPass({ timeout: 3000 });

  // Click Briefing Templates — should scroll back to top (scrollTop < scrollAfterIntegrations)
  await sidebar.getByRole('button', { name: 'Briefing Templates' }).click();
  await expect(async () => {
    const a = await content.evaluate(el => el.scrollTop);
    await page.waitForTimeout(80);
    const b = await content.evaluate(el => el.scrollTop);
    // Stable and less than where Integrations left us
    expect(b).toBe(a);
    expect(b).toBeLessThan(scrollAfterIntegrations);
  }).toPass({ timeout: 3000 });
});

test('section headers visible on settings page', async ({ page }) => {
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Both section headings should be visible
  await expect(page.getByRole('heading', { name: 'Briefing Templates' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible();
});

test('config entries displayed and editable', async ({ page }) => {
  // Ensure at least one briefing.* config entry exists (page only renders briefing.* keys)
  const configs: { name: string; value: string }[] = await apiGet('/api/config');
  if (!configs.find(c => c.name.startsWith('briefing.'))) {
    await apiPost('/api/config', { name: 'briefing.test', value: 'test_value' });
    testCreatedConfigKeys.add('briefing.test');
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

test('Ctrl+S saves the focused entry', async ({ page }) => {
  // Ensure at least one briefing config entry exists
  const configs: { name: string; value: string }[] = await apiGet('/api/config');
  let briefingEntry = configs.find(c => c.name.startsWith('briefing.'));
  if (!briefingEntry) {
    await apiPost('/api/config', { name: 'briefing.delegation', value: 'original' });
    testCreatedConfigKeys.add('briefing.delegation');
    briefingEntry = { name: 'briefing.delegation', value: 'original' };
  }

  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  const firstSection = page.locator('.settings-entry').first();
  const textarea = firstSection.locator('textarea');
  const originalValue = await textarea.inputValue();

  // Modify the value
  await textarea.fill(originalValue + ' ctrl-s-test');

  // Save should be enabled
  await expect(firstSection.getByRole('button', { name: 'Save' })).toBeEnabled();

  // Press Ctrl+S
  await textarea.focus();
  await page.keyboard.press('Control+s');

  // Should show success feedback
  await expect(firstSection.getByText('Saved')).toBeVisible({ timeout: 5000 });

  // Restore original value
  await textarea.fill(originalValue);
  await textarea.focus();
  await page.keyboard.press('Control+s');
  await expect(firstSection.getByText('Saved')).toBeVisible({ timeout: 5000 });
});

test('navigation guard fires dialog when navigating away with unsaved edits', async ({ page }) => {
  // Ensure at least one briefing config entry exists
  const configs: { name: string; value: string }[] = await apiGet('/api/config');
  if (!configs.find(c => c.name.startsWith('briefing.'))) {
    await apiPost('/api/config', { name: 'briefing.delegation', value: 'original' });
    testCreatedConfigKeys.add('briefing.delegation');
  }

  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Make an edit so the nav guard is active
  const firstSection = page.locator('.settings-entry').first();
  const textarea = firstSection.locator('textarea');
  const originalValue = await textarea.inputValue();
  await textarea.fill(originalValue + ' nav-guard-test');
  await expect(firstSection.getByRole('button', { name: 'Save' })).toBeEnabled();

  // Intercept and accept the browser dialog
  let dialogFired = false;
  page.once('dialog', async (dialog) => {
    dialogFired = true;
    await dialog.accept();
  });

  // Attempt to navigate away via hash — router guard should prompt
  await page.evaluate(() => { window.location.hash = '#/'; });
  await page.waitForTimeout(300);
  expect(dialogFired).toBe(true);
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
