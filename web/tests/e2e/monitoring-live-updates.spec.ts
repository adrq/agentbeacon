import { test, expect } from '@playwright/test';
import { DELAYED_WORKFLOW } from '../fixtures/workflows';

test('execution monitoring live updates', async ({ page }) => {
  await page.goto('/');

  await page.getByTestId('new-execution-button').click();

  await expect(page.getByTestId('workflow-modal')).toBeVisible();

  const textarea = page.locator('textarea');
  await textarea.fill(DELAYED_WORKFLOW);

  await page.getByRole('button', { name: /submit/i }).click();

  await expect(page).toHaveURL(/\/run\/[0-9a-f-]{36}/);

  await expect(page.getByTestId('status-bar')).toBeVisible();

  await expect(page.getByTestId('dag-node').first()).toBeVisible();

  const dagNode = page.getByTestId('dag-node').first();

  await expect(dagNode).not.toHaveClass(/completed|green/i, {
    timeout: 5000,
  });

  await expect(dagNode).toHaveClass(/completed|green/i, {
    timeout: 30000,
  });

  await expect(page.getByTestId('status-bar')).toContainText(/completed/i);

  let pollingRequestCount = 0;
  page.on('request', (req) => {
    if (req.url().includes('/api/executions/')) {
      pollingRequestCount++;
    }
  });

  await page.waitForTimeout(5000);

  expect(pollingRequestCount).toBe(0);
});
