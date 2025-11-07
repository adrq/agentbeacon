import { test, expect } from '@playwright/test';
import { INVALID_WORKFLOW } from '../fixtures/workflows';

test('invalid YAML error display', async ({ page }) => {
  await page.goto('/');

  await page.getByTestId('new-execution-button').click();

  await expect(page.getByTestId('workflow-modal')).toBeVisible();

  const textarea = page.locator('textarea');
  await textarea.fill(INVALID_WORKFLOW);

  await page.getByRole('button', { name: /submit/i }).click();

  await expect(page.getByTestId('error-message')).toBeVisible();

  const errorText = await page.getByTestId('error-message').textContent();
  expect(errorText).toMatch(/validation|failed|invalid/i);

  await expect(page.getByTestId('workflow-modal')).toBeVisible();

  const textareaValue = await textarea.inputValue();
  expect(textareaValue).toBe(INVALID_WORKFLOW);

  expect(page.url()).toBe(new URL('/', page.url()).href);
});
