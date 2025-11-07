import { test, expect } from '@playwright/test';
import { VALID_WORKFLOW } from '../fixtures/workflows';

test('workflow submission flow', async ({ page }) => {
  await page.goto('/');

  await page.getByTestId('new-execution-button').click();

  await expect(page.getByTestId('workflow-modal')).toBeVisible();

  const textarea = page.locator('textarea');
  await textarea.fill(VALID_WORKFLOW);

  await page.getByRole('button', { name: /submit/i }).click();

  await expect(page).toHaveURL(/\/run\/[0-9a-f-]{36}/);

  await expect(page.getByTestId('dag-visualization')).toBeVisible();

  await expect(page.getByTestId('logs-panel')).toContainText('execution_started');
});
