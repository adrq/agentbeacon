import { test, expect } from '@playwright/test';
import { DELAYED_WORKFLOW } from '../fixtures/workflows';

test('network error retry', async ({ page }) => {
  await page.goto('/');

  await page.getByTestId('new-execution-button').click();

  await expect(page.getByTestId('workflow-modal')).toBeVisible();

  const textarea = page.locator('textarea');
  await textarea.fill(DELAYED_WORKFLOW);

  let pollCount = 0;
  let abortCount = 0;

  await page.route(/\/api\/executions\//, (route) => {
    pollCount++;
    if (pollCount === 1) {
      route.continue();
    } else {
      abortCount++;
      route.abort();
    }
  });

  await page.getByRole('button', { name: /submit/i }).click();

  await expect(page).toHaveURL(/\/run\/[0-9a-f-]{36}/);

  await page.waitForResponse(
    (resp) => resp.url().includes('/api/executions/') && resp.ok(),
    { timeout: 10000 }
  );

  await page.waitForTimeout(2500);

  expect(abortCount).toBeGreaterThan(0);

  await expect(page.getByTestId('error-indicator')).toBeVisible();

  const errorText = await page.getByTestId('error-indicator').textContent();
  expect(errorText).toMatch(/failed|error/i);

  await expect(page.getByTestId('dag-visualization')).toBeVisible();
  await expect(page.getByTestId('logs-panel')).toBeVisible();

  await page.unroute(/\/api\/executions\//);

  await page.waitForResponse(
    (resp) => resp.url().includes('/api/executions/') && resp.ok(),
    { timeout: 5000 }
  );

  await expect(page.getByTestId('error-indicator')).not.toBeVisible();
});
