import { test, expect } from '@playwright/test';
import { apiGet, apiPost, apiDelete } from './helpers';

test('create MCP server via JSON textarea', async ({ page }) => {
  // Navigate to settings page
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Find MCP Servers section
  await expect(page.getByRole('heading', { name: 'MCP Servers' })).toBeVisible();

  // Click + Add button
  await page.getByRole('button', { name: '+ Add' }).click();

  // Verify the dialog opened
  await expect(page.getByRole('heading', { name: 'Add MCP Server' })).toBeVisible({ timeout: 5000 });

  // Prepare valid stdio JSON config
  const validConfig = `{
  "test-playwright": {
    "type": "stdio",
    "command": "npx",
    "args": ["@playwright/mcp@latest", "--headless"]
  }
}`;

  // Fill the textarea
  const textarea = page.locator('textarea#mcp-config');
  await expect(textarea).toBeVisible();
  await textarea.fill(validConfig);

  // Click Create button
  await page.getByRole('button', { name: 'Create' }).click();

  // Verify dialog closed
  await expect(page.getByRole('heading', { name: 'Add MCP Server' })).not.toBeVisible({ timeout: 5000 });

  // Verify server appears in the list
  const serverCard = page.locator('.mcp-card').filter({ hasText: 'test-playwright' });
  await expect(serverCard).toBeVisible({ timeout: 5000 });

  // Verify name and type badge
  await expect(serverCard.locator('.mcp-name')).toHaveText('test-playwright');
  await expect(serverCard.locator('.mcp-type-badge')).toHaveText('stdio');

  // Cleanup: delete the server
  const servers: { id: string; name: string }[] = await apiGet('/api/mcp-servers');
  const testServer = servers.find(s => s.name === 'test-playwright');
  if (testServer) {
    await apiDelete(`/api/mcp-servers/${testServer.id}`);
  }
});

test('edit existing MCP server with pre-populated JSON', async ({ page }) => {
  // Create a server via API first
  const createResponse = await apiPost('/api/mcp-servers', {
    name: 'edit-test-server',
    transport_type: 'stdio',
    config: {
      command: 'python',
      args: ['-m', 'mcp_server'],
    },
  });
  const serverId = createResponse.id;

  try {
    // Navigate to settings page
    await page.goto('/#/settings');
    await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

    // Find the server card
    const serverCard = page.locator('.mcp-card').filter({ hasText: 'edit-test-server' });
    await expect(serverCard).toBeVisible({ timeout: 5000 });

    // Click Edit button
    await serverCard.getByRole('button', { name: 'Edit' }).click();

    // Verify the edit dialog opened
    await expect(page.getByRole('heading', { name: 'Edit MCP Server' })).toBeVisible({ timeout: 5000 });

    // Verify textarea is pre-populated with correct JSON
    const textarea = page.locator('textarea#mcp-config');
    const currentValue = await textarea.inputValue();
    expect(currentValue).toContain('edit-test-server');
    expect(currentValue).toContain('stdio');
    expect(currentValue).toContain('python');

    // Modify the JSON
    const updatedConfig = `{
  "edit-test-server": {
    "type": "stdio",
    "command": "node",
    "args": ["server.js"]
  }
}`;
    await textarea.fill(updatedConfig);

    // Click Save button (scoped to the dialog)
    const dialog = page.getByRole('dialog', { name: 'Edit MCP Server' });
    await dialog.getByRole('button', { name: 'Save' }).click();

    // Verify dialog closed
    await expect(page.getByRole('heading', { name: 'Edit MCP Server' })).not.toBeVisible({ timeout: 5000 });

    // Verify the update is reflected in the UI
    const updatedCard = page.locator('.mcp-card').filter({ hasText: 'edit-test-server' });
    await expect(updatedCard).toBeVisible();
    await expect(updatedCard.locator('.mcp-summary')).toContainText('node server.js');
  } finally {
    // Cleanup: delete the server
    await apiDelete(`/api/mcp-servers/${serverId}`);
  }
});

test('invalid JSON shows error message', async ({ page }) => {
  // Navigate to settings page
  await page.goto('/#/settings');
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible({ timeout: 5000 });

  // Click + Add button
  await page.getByRole('button', { name: '+ Add' }).click();

  // Verify the dialog opened
  await expect(page.getByRole('heading', { name: 'Add MCP Server' })).toBeVisible({ timeout: 5000 });

  // Type invalid JSON
  const textarea = page.locator('textarea#mcp-config');
  await textarea.fill('{ this is not valid json }');

  // Click Create button
  await page.getByRole('button', { name: 'Create' }).click();

  // Verify error message appears
  const errorMessage = page.locator('.form-error[role="alert"]');
  await expect(errorMessage).toBeVisible({ timeout: 3000 });
  await expect(errorMessage).toContainText('Invalid JSON');

  // Dialog should still be open (not closed on error)
  await expect(page.getByRole('heading', { name: 'Add MCP Server' })).toBeVisible();

  // Close the dialog
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByRole('heading', { name: 'Add MCP Server' })).not.toBeVisible({ timeout: 5000 });
});
