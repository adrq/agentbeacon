import { test, expect } from '@playwright/test';

test('mobile: home page loads at mobile viewport', async ({ page }) => {
  await page.goto('/');

  // Nav bar should exist and be positioned at the bottom as a horizontal bar
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(nav).toBeVisible();

  const navBox = await nav.boundingBox();
  const viewport = page.viewportSize()!;
  expect(navBox).toBeTruthy();
  // Nav bar bottom edge should be at or near the viewport bottom
  expect(navBox!.y + navBox!.height).toBeGreaterThan(viewport.height - 80);
});

test('mobile: executions page shows list at full width', async ({ page }) => {
  await page.goto('/#/executions');

  // The left panel (exec list) should be visible and take full width
  const leftPanel = page.locator('.left-panel');
  await expect(leftPanel).toBeVisible();
  const box = await leftPanel.boundingBox();
  const viewport = page.viewportSize()!;
  expect(box).toBeTruthy();
  expect(box!.width).toBeGreaterThan(viewport.width * 0.9);
});
