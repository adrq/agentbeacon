import { test, expect } from '@playwright/test';
import { VALID_WORKFLOW } from '../fixtures/workflows';

test('dashboard recent activity', async ({ page }) => {
  await page.goto('/');

  await page.getByTestId('new-execution-button').click();

  await expect(page.getByTestId('workflow-modal')).toBeVisible();

  const textarea = page.locator('textarea');
  await textarea.fill(VALID_WORKFLOW);

  await page.getByRole('button', { name: /submit/i }).click();

  await expect(page).toHaveURL(/\/run\/[0-9a-f-]{36}/);

  const urlParts = page.url().split('/');
  const executionId = urlParts[urlParts.length - 1];

  await expect(page.getByTestId('status-bar')).toContainText(/completed/i, {
    timeout: 30000,
  });

  await page.goto('/');

  await expect(page.getByTestId('recent-activity')).toBeVisible();

  const activityEntry = page
    .getByTestId('recent-activity')
    .locator('text=test-workflow')
    .first();
  await expect(activityEntry).toBeVisible();

  const completedBadge = page.getByTestId('recent-activity').locator('.badge', {
    hasText: /completed/i,
  });
  await expect(completedBadge).toBeVisible();
  const badgeClass = await completedBadge.getAttribute('class');
  expect(badgeClass).toMatch(/green|success|completed/i);

  await activityEntry.click();

  await expect(page).toHaveURL(new RegExp(`/run/${executionId}`));

  await expect(page.getByTestId('dag-visualization')).toBeVisible();

  await expect(page.getByTestId('logs-panel')).toContainText('execution_completed');
});
